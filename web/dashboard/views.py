import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import storage, system_health
from .engine import (current_service, engine_health, get_service, grab_frame, list_live_sources,
                     list_video_files)
from .models import (AuditLog, CameraSettings, Review, SpeedCalibration, SystemSettings, UserPreferences,
                     VehicleRecord, Violation)
from .plate_search import normalize_query, rank_plates, split_queries
from .serializers import (CalibrationSerializer, PasswordChangeSerializer, PauseSerializer, PlateSearchSerializer,
                          PreferencesSerializer, ReviewSerializer, SeekSerializer, SystemSettingsSerializer,
                          VehicleDeleteSerializer,
                          SourceDistancesSerializer, SourceKeySerializer, SourceSerializer,
                          VehicleQuerySerializer, VehicleRecordSerializer)

# Rows sent on first load / per poll
_INITIAL_ROWS = 100
_MAX_NEW_ROWS = 200
# Plate search: lowest similarity shown, and how many matching plates / rows are returned
_SEARCH_MIN_SCORE = 0.6
_SEARCH_MAX_PLATES = 300
_SEARCH_MAX_ROWS = 300
# Violations sent per poll for the in-page notifications (the others are only counted)
_MAX_NOTIFY_ROWS = 3
# Rows per page when the violations page lists every record (no plate typed)
_BROWSE_PAGE_ROWS = 100
# Violation statistics cover this many days, today included
_STATS_DAYS = 30


def _client_ip(request):
    return request.META.get("REMOTE_ADDR")


def _audit(request, action, detail=""):
    AuditLog.objects.create(user=request.user, action=action, detail=detail[:255], ip_address=_client_ip(request))


def _conflict(error):
    """409: the request is valid, but the video engine cannot do it right now (not ready, wrong mode...)."""
    return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)


# ---------- pages ----------

class LoginView(LoginView):
    template_name = "dashboard/login.html"
    redirect_authenticated_user = True


def lockout(request, original_response=None, credentials=None):
    """django-axes: too many failed logins for this username + IP."""
    return render(request, "dashboard/login.html", {
        "locked_out": True,
        "cooloff_minutes": int(settings.AXES_COOLOFF_TIME.total_seconds() // 60),
    }, status=429)


@require_GET
def dashboard(request):
    get_service(request.session.session_key)   # start detection if it is not running yet
    prefs = UserPreferences.load(request.user)
    return render(request, "dashboard/dashboard.html", {
        "video_files": list_video_files(),
        "live_sources": [(key, label) for key, (label, _) in list_live_sources().items()],
        "saved": CameraSettings.load(),
        "rules": SystemSettings.load(),
        "prefs": prefs,
        "prefs_data": PreferencesSerializer(prefs).data,
    })


def _source_options():
    """{"live": [...], "file": [...]} of (value, label) for the source dropdown: the IP camera and the add-on
    cameras, the files in the input folder, and any other source records were saved from (e.g. a file
    since removed). The value is "<source_mode>:<source_name>", as the records store it and as
    PlateSearchSerializer.source takes it (add-on cameras are saved under their label, the IP camera as "")."""
    names = {"live": {"", *(label for label, _ in list_live_sources().values())}, "file": set(list_video_files())}
    for mode, name in VehicleRecord.objects.order_by().values_list("source_mode", "source_name").distinct():
        names.setdefault(mode, set()).add(name)
    label = lambda mode, name: name or ("IP camera (RTSP)" if mode == "live" else "Video file")
    return {mode: [(f"{mode}:{name}", label(mode, name)) for name in sorted(found, key=lambda n: (n != "", n.lower()))]
            for mode, found in names.items()}


def _province_names():
    """(Thai name, English name) of every province for the dropdown: Bangkok first, then the others in
    Thai alphabetical order, as PROVINCE_NAMES_EN lists them (Python cannot sort Thai text correctly)."""
    from province import PROVINCE_NAMES_EN

    bangkok = "กรุงเทพมหานคร"
    return [(bangkok, PROVINCE_NAMES_EN[bangkok])] + [item for item in PROVINCE_NAMES_EN.items() if item[0] != bangkok]


@require_GET
def plate_search(request):
    """Plate search on its own."""
    return render(request, "dashboard/search.html", {"provinces": _province_names(), "sources": _source_options()})


@require_GET
def violations(request):
    """Violation statistics, with the plate search under them."""
    return render(request, "dashboard/violations.html",
                  {"provinces": _province_names(), "sources": _source_options()})


def _detection_models(config):
    """The models detection loads (AppConfig paths) and what each one is for, for the system status page."""
    def found(path):   # AppConfig paths are relative to the project root
        return (Path(settings.PROJECT_DIR) / path).exists()

    rider_found = found(config.rider_model_path)
    models = [
        ("Motorcycle detector", config.motorcycle_model_path, "finds motorcycles in the frame"),
        ("Plate detector", config.plate_model_path, "finds the licence plate on each motorcycle"),
        ("Plate OCR", config.plate_ocr_model_path, "reads the plate's letters and number"),
        ("Rider classifier", config.rider_model_path, "counts riders and checks their helmets"),
        ("Helmet detector", config.helmet_model_path,
         "not used while the rider classifier is there" if rider_found else "helmet / no helmet on each head"),
    ]
    return [{"name": name, "file": Path(path).name, "purpose": purpose, "found": found(path)}
            for name, path, purpose in models]


@require_GET
def system_status(request):
    """Admin: camera and system health (it only checks detection, it never starts it)."""
    from app_config import AppConfig

    models = _detection_models(AppConfig.from_env())
    return render(request, "dashboard/system.html",
                  {"models": models, "models_missing": sum(not m["found"] for m in models)})


# ---------- API used by the dashboard page (Django REST Framework) ----------
# Errors are JSON: {"detail": "..."} for a whole request, or {"<field>": ["..."]} from a serializer.

class StatusView(APIView):
    def get(self, request):
        """Engine state, playback position and today's vehicle counts (polled every second)."""
        data = get_service(request.session.session_key).status()
        today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        records = VehicleRecord.objects.filter(detected_at__gte=today)
        total = records.count()
        violations = records.filter(is_violation=True).count()
        data["today"] = {"total": total, "violation": violations, "normal": total - violations}
        return Response(data)


class VehicleListView(APIView):
    def get(self, request):
        """New rows after ?after=<id>, or the latest rows on first load (newest first)."""
        query = VehicleQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        after = query.validated_data["after"]

        records = VehicleRecord.objects.select_related("reviewed_by")
        if after > 0:
            records = records.filter(id__gt=after)[:_MAX_NEW_ROWS]
        else:
            records = records[:_INITIAL_ROWS]
        return Response({"rows": VehicleRecordSerializer(records, many=True).data})


class NewViolationsView(APIView):
    def get(self, request):
        """Violations recorded after ?after=<id>, for the in-page notifications on every page: the newest few
        as rows, and how many there are. With after=0 only the id to start from is sent, so violations
        from before the page opened are not announced."""
        query = VehicleQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        after = query.validated_data["after"]
        last_id = VehicleRecord.objects.aggregate(last=Max("id"))["last"] or 0
        if after == 0:
            return Response({"last_id": last_id, "total": 0, "rows": []})
        new = VehicleRecord.objects.filter(id__gt=after, is_violation=True)
        return Response({"last_id": last_id, "total": new.count(),
                         "rows": VehicleRecordSerializer(new[:_MAX_NOTIFY_ROWS], many=True).data})


def _filtered(records, filters):
    """records narrowed by the list's filters (validated PlateSearchSerializer data)."""
    if filters["violations_only"]:
        records = records.filter(is_violation=True)
    if filters["province"]:
        records = records.filter(province_text=filters["province"])
    if filters["source"]:
        mode, one_source, name = filters["source"].partition(":")
        records = records.filter(source_mode=mode)
        if one_source:   # "<mode>:<name>" is one camera or file; "<mode>" alone is the whole group
            records = records.filter(source_name=name)
    return records


def _plate_matches(q, filters) -> list:
    """Records whose plate looks like q, best match first (see plate_search.py)."""
    records = _filtered(VehicleRecord.objects.exclude(plate_text="").select_related("reviewed_by"), filters)
    # Score each distinct plate once; a plate seen many times is still one comparison
    plates = records.order_by().values_list("plate_text", flat=True).distinct()
    scores = rank_plates(q, plates, _SEARCH_MIN_SCORE)
    best = sorted(scores, key=scores.get, reverse=True)[:_SEARCH_MAX_PLATES]

    matches = []
    for i in range(0, len(best), 500):   # stay under SQLite's limit on query parameters
        matches += records.filter(plate_text__in=best[i:i + 500])
    matches.sort(key=lambda r: (-scores[r.plate_text], -r.id))
    return matches


def _all_records(filters):
    """Every record (unreadable plates too), newest first: the list shown before a plate is typed."""
    return _filtered(VehicleRecord.objects.select_related("reviewed_by"), filters)


class PlateSearchView(APIView):
    def get(self, request):
        """Vehicles whose plate looks like the query, best match first. With no query, every record,
        newest first, a page at a time (next_before: the "before" of the next page, or null)."""
        query = PlateSearchSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        q, before = filters["q"], filters["before"]
        if not q:
            records = _all_records(filters)
            total = records.count()
            if before:
                records = records.filter(id__lt=before)
            page = list(records[:_BROWSE_PAGE_ROWS])
            next_before = page[-1].id if len(page) == _BROWSE_PAGE_ROWS else None
            return Response({"query": "", "total": total, "next_before": next_before,
                             "rows": VehicleRecordSerializer(page, many=True).data})

        matches = _plate_matches(q, filters)
        rows = VehicleRecordSerializer(matches[:_SEARCH_MAX_ROWS], many=True).data
        query = ", ".join(normalize_query(plate) for plate in split_queries(q))
        return Response({"query": query, "total": len(matches), "next_before": None, "rows": rows})


class VehicleReviewView(APIView):
    def post(self, request, pk: int):
        """Confirm or reject one recorded event (or undo the review), with an optional note."""
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = get_object_or_404(VehicleRecord, pk=pk)
        record.review = serializer.validated_data["review"]
        reviewed = record.review != Review.PENDING
        record.review_note = serializer.validated_data["note"] if reviewed else ""
        record.reviewed_at = timezone.now() if reviewed else None
        record.reviewed_by = request.user if reviewed else None
        record.save(update_fields=["review", "review_note", "reviewed_at", "reviewed_by"])
        _audit(request, f"review_{record.review}", f"#{record.pk} {record.plate_text or '-'}")
        return Response(VehicleRecordSerializer(record).data)


class VehicleClearView(APIView):
    def post(self, request):
        """Delete every vehicle record and its evidence snapshot (live view; needs delete_records)."""
        deleted = storage.delete_records(VehicleRecord.objects.values_list("id", flat=True))
        _audit(request, "clear_vehicles", f"{deleted} records")
        return Response({"deleted": deleted})


class VehicleDeleteView(APIView):
    def post(self, request):
        """Admin: delete the selected records, or every record of the list as filtered on the
        violations page (all matches of a plate search, not only the rows shown)."""
        serializer = VehicleDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data["all"]:
            ids = data["ids"]
        elif data["q"]:
            ids = [r.id for r in _plate_matches(data["q"], data)]
        else:
            ids = _all_records(data).values_list("id", flat=True)
        deleted = storage.delete_records(ids)
        which = "whole list" if data["all"] else "selected"
        _audit(request, "delete_vehicles", f"{deleted} records ({which})")
        return Response({"deleted": deleted})


class SourceView(APIView):
    def post(self, request):
        """Switch to a video file or a live camera, and remember the choice for the next start."""
        serializer = SourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mode = serializer.validated_data["mode"]
        video_file = serializer.validated_data["file"]
        live_source = serializer.validated_data["live"]

        service = get_service()
        try:
            service.switch(mode, video_file, live_source)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return _conflict(e)

        saved = CameraSettings.load()
        saved.mode = mode
        saved.video_file = video_file
        saved.live_source = live_source
        saved.updated_by = request.user
        saved.save()
        _audit(request, "switch_source", service.describe_source())
        return Response(service.status())


class PauseView(APIView):
    def post(self, request):
        """Pause or resume the video file: {"paused": true/false} sets it, {} flips it."""
        serializer = PauseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service = get_service()
        try:
            paused = service.toggle_pause(serializer.validated_data.get("paused"))
        except RuntimeError as e:
            return _conflict(e)
        _audit(request, "pause" if paused else "resume", service.describe_source())
        return Response({"paused": paused})


class SeekView(APIView):
    def post(self, request):
        """Jump to a position in the video file."""
        serializer = SeekSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            get_service().seek(serializer.validated_data["pos_ms"])
        except RuntimeError as e:
            return _conflict(e)
        return Response({"ok": True})


class CalibrationView(APIView):
    def get(self, request):
        """Speed calibration of the current source (points in frame pixels)."""
        return Response(get_service().calibration())


class CalibrationFrameView(APIView):
    def get(self, request):
        """The current frame without overlays, as JPEG, for clicking the calibration points on."""
        try:
            jpeg = get_service().calibration_frame()
        except RuntimeError as e:
            return _conflict(e)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        return response


def _save_calibration_points(request, service):
    """Save the 4 points and real size clicked on the current source's frame (live view and system page)."""
    frame_size = service.frame_size()
    if frame_size is None:
        return _conflict("No video signal to calibrate on")

    serializer = CalibrationSerializer(data=request.data, context={"frame_size": frame_size})
    serializer.is_valid(raise_exception=True)
    points = serializer.validated_data["points"]
    width_m = serializer.validated_data["width_m"]
    length_m = serializer.validated_data["length_m"]

    try:
        service.save_calibration(points, width_m, length_m, request.user)
    except ValueError:
        # The speed estimator rejects shapes that are not a convex 4-sided outline
        return Response({"points": ["The 4 points must outline a rectangle on the road, clicked in order"]},
                        status=status.HTTP_400_BAD_REQUEST)
    except RuntimeError as e:
        return _conflict(e)
    _audit(request, "speed_calibration", f"{service.describe_source()}: {width_m:g} x {length_m:g} m")
    return Response(service.calibration())


class CalibrationSaveView(APIView):
    def post(self, request):
        """Save the speed calibration of the current source."""
        return _save_calibration_points(request, get_service())


class CalibrationRemoveView(APIView):
    def post(self, request):
        """Remove the speed calibration of the current source (speeds are no longer measured)."""
        service = get_service()
        try:
            service.remove_calibration(request.user)
        except RuntimeError as e:
            return _conflict(e)
        _audit(request, "speed_calibration_removed", service.describe_source())
        return Response(service.calibration())


class SourceCalibrationView(APIView):
    def post(self, request):
        """Admin: change the real width and length of a source's saved calibration (not its points)."""
        serializer = SourceDistancesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key, width_m, length_m = (serializer.validated_data[f] for f in ("source_key", "width_m", "length_m"))
        updated = (SpeedCalibration.objects.filter(source_key=key, points__isnull=False)
                   .update(width_m=width_m, length_m=length_m, updated_by=request.user, updated_at=timezone.now()))
        if not updated:
            return _conflict("This source has no saved calibration; staff set the points from the live view")
        _reload_calibration(key)
        _audit(request, "speed_calibration", f"{key}: {width_m:g} x {length_m:g} m")
        return Response({"source_key": key, "width_m": width_m, "length_m": length_m})


def _reload_calibration(source_key):
    """Apply a calibration changed on the system status page, when detection is on (it loads it on start)."""
    service = current_service()
    if service is not None:
        service.reload_calibration(source_key)


# Size of the last frame sent for redrawing a source's points, so the points saved for it can be checked
_REDRAW_FRAME_SIZE = "calibration-frame-size:{}"
_REDRAW_FRAME_KEEP_S = 60 * 60


class SourceCalibrationFrameView(APIView):
    def get(self, request):
        """Admin: a current frame of a source to redraw its points on. Detection need not be on: the
        source is opened on its own unless detection is already showing it."""
        serializer = SourceKeySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["source_key"]
        service = current_service()
        try:
            if (service is not None and service.state == service.RUNNING and service.source_key() == key
                    and service.has_signal()):
                jpeg, size = service.calibration_frame(), service.frame_size()
            else:
                frame = grab_frame(key)
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if not ok:
                    raise RuntimeError("Could not capture a frame")
                jpeg, size = buf.tobytes(), (frame.shape[1], frame.shape[0])
        except RuntimeError as e:
            return _conflict(e)
        cache.set(_REDRAW_FRAME_SIZE.format(key), size, _REDRAW_FRAME_KEEP_S)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        return response


class SourceCalibrationPointsView(APIView):
    def post(self, request):
        """Admin: save the 4 points (and real size) redrawn on the frame from SourceCalibrationFrameView."""
        from speed import SpeedEstimator

        source = SourceKeySerializer(data=request.data)
        source.is_valid(raise_exception=True)
        key = source.validated_data["source_key"]
        frame_size = cache.get(_REDRAW_FRAME_SIZE.format(key))
        if frame_size is None:
            return _conflict("The frame expired; open the editor again")

        serializer = CalibrationSerializer(data=request.data, context={"frame_size": frame_size})
        serializer.is_valid(raise_exception=True)
        points, width_m, length_m = (serializer.validated_data[f] for f in ("points", "width_m", "length_m"))
        try:
            # Same shape check detection applies (convex, in order, not tiny)
            SpeedEstimator().set_calibration([tuple(p) for p in points], (width_m, length_m))
        except ValueError:
            return Response({"points": ["The 4 points must outline a rectangle on the road, clicked in order"]},
                            status=status.HTTP_400_BAD_REQUEST)
        SpeedCalibration.objects.update_or_create(
            source_key=key,
            defaults={"points": points, "width_m": width_m, "length_m": length_m, "updated_by": request.user})
        _reload_calibration(key)
        _audit(request, "speed_calibration", f"{key}: {width_m:g} x {length_m:g} m (points redrawn)")
        return Response({"source_key": key, "points": points, "width_m": width_m, "length_m": length_m})


class SourceCalibrationRemoveView(APIView):
    def post(self, request):
        """Admin: remove a source's speed calibration (speeds are no longer measured on it)."""
        serializer = SourceKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["source_key"]
        # Kept as an empty row so the IP camera's .env fallback is not applied again (see engine)
        SpeedCalibration.objects.update_or_create(
            source_key=key, defaults={"points": None, "width_m": None, "length_m": None, "updated_by": request.user})
        _reload_calibration(key)
        _audit(request, "speed_calibration_removed", key)
        return Response({"source_key": key})


class SystemSettingsView(APIView):
    def post(self, request):
        """Admin: change the violation rules and the retention limits. New rules apply at once when
        detection is on; new limits apply at the next clean-up."""
        system = SystemSettings.load()
        serializer = SystemSettingsSerializer(system, data=request.data)
        serializer.is_valid(raise_exception=True)
        changed = [f"{name}={value}" for name, value in serializer.validated_data.items()
                   if getattr(system, name) != value]
        system = serializer.save(updated_by=request.user)
        service = current_service()
        if service is not None:
            service.apply_rules(system.max_riders, float(system.speed_limit_kmh))
        if changed:
            _audit(request, "system_settings", ", ".join(changed))
        return Response(SystemSettingsSerializer(system).data)


class SettingsCleanupView(APIView):
    def post(self, request):
        """Admin: apply the retention limits now instead of waiting for the next scheduled clean-up."""
        result = storage.clean_up()
        _audit(request, "storage_cleanup",
               f"{result['records_deleted']} records, photos of {result['photos_removed']} records")
        return Response(result)


class PreferencesView(APIView):
    def post(self, request):
        """Save this account's preferences; the Settings pop-up sends only the fields its page shows."""
        serializer = PreferencesSerializer(UserPreferences.load(request.user), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PasswordView(APIView):
    # Limits guessing the current password from a session left open
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def post(self, request):
        """Change the password of the logged-in account; this session stays logged in, others end."""
        serializer = PasswordChangeSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        _audit(request, "change_password")
        return Response({"ok": True})


class StatsView(APIView):
    def get(self, request):
        """Violation statistics for the search page: totals, violations by type and per day."""
        now = timezone.localtime()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=_STATS_DAYS - 1)
        records = VehicleRecord.objects.filter(detected_at__gte=start)
        violations = records.filter(is_violation=True)

        per_day = {row["day"]: row for row in records
                   .annotate(day=TruncDate("detected_at", tzinfo=timezone.get_current_timezone()))
                   .values("day")
                   .annotate(total=Count("id"), violation=Count("id", filter=Q(is_violation=True)))}
        days = [(start + timedelta(days=i)).date() for i in range(_STATS_DAYS)]
        total = records.count()
        return Response({
            "days": _STATS_DAYS,
            "today": violations.filter(detected_at__gte=today).count(),
            "week": violations.filter(detected_at__gte=today - timedelta(days=6)).count(),
            "violation": violations.count(),
            "total": total,
            "pending_review": violations.filter(review=Review.PENDING).count(),
            "confirmed": violations.filter(review=Review.CONFIRMED).count(),
            "by_type": [{"label": v.label, "count": violations.filter(violations__contains=v.value).count()}
                        for v in Violation],
            "per_day": [{"date": d.strftime("%d/%m"),
                         "total": per_day.get(d, {}).get("total", 0),
                         "violation": per_day.get(d, {}).get("violation", 0)} for d in days],
        })


class CameraStatusView(APIView):
    def get(self, request):
        """Admin: only whether each live camera answers, for the camera notifications (notify.js).
        Uses the same check as the system status page, so it is cheap to ask often."""
        from app_config import AppConfig

        cameras, checked_at = system_health.cameras(AppConfig.from_env().rtsp_url)
        return Response({
            "cameras": [{"key": c["key"], "label": c["label"], "state": c["state"], "detail": c["detail"]}
                        for c in cameras],
            "checked_at": datetime.fromtimestamp(checked_at, tz=timezone.get_current_timezone()),
        })


class SystemStatusView(APIView):
    def get(self, request):
        """Admin: detection engine, cameras, video files and stored records (polled every few seconds)."""
        from app_config import AppConfig

        config = AppConfig.from_env()
        # Detection is checked, never started, from here; the source is only known while it runs
        service = current_service()
        running = service is not None and service.state == service.RUNNING
        in_use = service.source_key() if running else None
        saved = {c.source_key: c for c in SpeedCalibration.objects.all()}

        def source_info(key):
            # Calibrated when one was saved for it; the IP camera may also use the one in .env (not editable here)
            calib = saved.get(key)
            from_env = key == "live:ip" and calib is None and bool(config.speed_calib_points)
            size = ({"points": calib.points, "width_m": calib.width_m, "length_m": calib.length_m}
                    if calib and calib.points else None)
            return {"source_key": key, "in_use": key == in_use, "speed_calibrated": bool(size) or from_env,
                    "calibration": size, "calibration_from_env": from_env}

        cameras, checked_at = system_health.cameras(config.rtsp_url)
        cameras = [{**c, **source_info(f"live:{c['key'] or 'ip'}")} for c in cameras]
        files = [{**f, **source_info(f"file:{f['name']}"),
                  "modified": datetime.fromtimestamp(f["modified"], tz=timezone.get_current_timezone())}
                 for f in system_health.video_files()]

        today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        records = VehicleRecord.objects.all()
        return Response({
            "engine": {"health": engine_health(), "status": service.status() if running else None},
            "cameras": cameras,
            "cameras_checked_at": datetime.fromtimestamp(checked_at, tz=timezone.get_current_timezone()),
            "video_files": files,
            "storage": storage.usage(),
            "records": {
                "total": records.count(),
                "today": records.filter(detected_at__gte=today).count(),
                "violations_today": records.filter(detected_at__gte=today, is_violation=True).count(),
                "pending_review": records.filter(is_violation=True, review=Review.PENDING).count(),
                "last_detected_at": records.order_by("-detected_at").values_list("detected_at", flat=True).first(),
            },
        })


# ---------- video and images (not JSON, so plain Django views) ----------

@require_GET
def stream(request):
    """MJPEG stream of the annotated video (or the No Signal card)."""
    session = request.session
    viewer = session.session_key
    interval = 1.0 / settings.STREAM_FPS

    def frames():
        last = None
        last_sent_t = 0.0
        next_check = time.time() + 1
        # Each frame is followed by the next part's header: browsers draw a frame only once that header
        # arrives, so a paused video's single frame would otherwise stay black
        part_head = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        yield part_head
        while True:
            # A stream outlives its page; stop it once the session has expired or the user logged out.
            # Checked every second so it cannot restart detection just after logout stopped it
            if time.time() >= next_check:
                next_check = time.time() + 1
                if not session.exists(session.session_key) or session.get_expiry_date() <= timezone.now():
                    return
            # Looked up every frame: an open stream keeps detection on, and follows it if it restarted
            jpeg = get_service(viewer).jpeg()
            # An unchanged frame (paused video) is sent again every second: the server only notices a
            # closed page when it writes, and a stream that never writes would hold its thread forever
            if jpeg and (jpeg is not last or time.time() - last_sent_t >= 1.0):
                last, last_sent_t = jpeg, time.time()
                yield jpeg + b"\r\n" + part_head
            time.sleep(interval)

    response = StreamingHttpResponse(frames(), content_type="multipart/x-mixed-replace; boundary=frame")
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def snapshot(request, pk: int, field: str = "snapshot"):
    """An evidence photo: the motorcycle (field "snapshot") or its licence plate ("plate_snapshot")."""
    record = VehicleRecord.objects.filter(pk=pk).only(field).first()
    photo = getattr(record, field) if record is not None else ""
    if not photo:
        raise Http404
    media_root = Path(settings.MEDIA_ROOT).resolve()
    path = (media_root / photo).resolve()
    # Paths come from our own DB, but never serve anything outside MEDIA_ROOT
    if media_root not in path.parents or not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), content_type="image/jpeg")
