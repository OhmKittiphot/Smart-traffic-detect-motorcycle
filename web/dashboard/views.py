import json
import math
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .engine import get_service, list_live_sources, list_video_files
from .models import AuditLog, CameraSettings, SourceMode, VehicleRecord

# Rows sent on first load / per poll
_INITIAL_ROWS = 100
_MAX_NEW_ROWS = 200


def _client_ip(request):
    return request.META.get("REMOTE_ADDR")


def _audit(request, action, detail=""):
    AuditLog.objects.create(user=request.user, action=action, detail=detail[:255], ip_address=_client_ip(request))


class AdminLoginView(LoginView):
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
    get_service()   # make sure detection is starting even if run without run.py
    from app_config import AppConfig

    config = AppConfig.from_env()
    return render(request, "dashboard/dashboard.html", {
        "video_files": list_video_files(),
        "live_sources": [(key, label) for key, (label, _) in list_live_sources().items()],
        "saved": CameraSettings.load(),
        "speed_limit": config.speed_limit_kmh,
        "max_riders": config.max_riders,
    })


def _record_json(r: VehicleRecord) -> dict:
    return {
        "id": r.id,
        "time": timezone.localtime(r.detected_at).strftime("%H:%M:%S"),
        "date": timezone.localtime(r.detected_at).strftime("%d/%m/%Y"),
        "track_id": r.track_id,
        "plate": r.plate_text,
        "province": r.province_text,
        "plate_conf": round(r.plate_conf * 100) if r.plate_conf else None,
        "riders": r.riders,
        "helmet": r.helmet_count,
        "no_helmet": r.no_helmet_count,
        "speed": r.speed_kmh,
        "violation": r.is_violation,
        "violations": r.violation_labels,
        "source": r.source_name or r.get_source_mode_display(),
        "snapshot": bool(r.snapshot),
    }


@require_GET
def api_status(request):
    status = get_service().status()
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    qs = VehicleRecord.objects.filter(detected_at__gte=today)
    total = qs.count()
    violations = qs.filter(is_violation=True).count()
    status["today"] = {"total": total, "violation": violations, "normal": total - violations}
    return JsonResponse(status)


@require_POST
def api_vehicles_clear(request):
    """Delete every vehicle record and its evidence snapshot."""
    media_root = Path(settings.MEDIA_ROOT).resolve()
    records = list(VehicleRecord.objects.values_list("id", "snapshot"))
    for _, snap in records:
        if not snap:
            continue
        path = (media_root / snap).resolve()
        if media_root in path.parents:
            path.unlink(missing_ok=True)
    # Delete exactly the rows whose files were removed; rows saved meanwhile keep their snapshots
    ids = [rid for rid, _ in records]
    for i in range(0, len(ids), 500):   # stay under SQLite's limit on query parameters
        VehicleRecord.objects.filter(id__in=ids[i:i + 500]).delete()
    # Remove day folders left empty
    snap_root = media_root / "snapshots"
    if snap_root.is_dir():
        for folder in snap_root.iterdir():
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
    _audit(request, "clear_vehicles", f"{len(records)} records")
    return JsonResponse({"deleted": len(records)})


@require_GET
def api_vehicles(request):
    """New rows after ?after=<id>, or the latest rows on first load (newest first)."""
    try:
        after = int(request.GET.get("after", "0"))
    except ValueError:
        after = 0
    qs = VehicleRecord.objects.all()
    rows = qs.filter(id__gt=after)[:_MAX_NEW_ROWS] if after > 0 else qs[:_INITIAL_ROWS]
    return JsonResponse({"rows": [_record_json(r) for r in rows]})


@require_POST
def api_source(request):
    try:
        body = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid request"}, status=400)
    mode = body.get("mode")
    video_file = body.get("file") or ""
    live_source = body.get("live") or ""
    if mode not in SourceMode.values or not isinstance(video_file, str) or not isinstance(live_source, str):
        return JsonResponse({"error": "invalid source"}, status=400)
    if mode == SourceMode.LIVE:
        video_file = ""
    else:
        live_source = ""

    try:
        get_service().switch(mode, video_file, live_source)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except RuntimeError as e:
        return JsonResponse({"error": str(e)}, status=409)

    saved = CameraSettings.load()
    saved.mode, saved.video_file, saved.live_source, saved.updated_by = mode, video_file, live_source, request.user
    saved.save()
    _audit(request, "switch_source", get_service().describe_source())
    return JsonResponse(get_service().status())


@require_POST
def api_pause(request):
    try:
        paused = get_service().toggle_pause()
    except RuntimeError as e:
        return JsonResponse({"error": str(e)}, status=409)
    _audit(request, "pause" if paused else "resume", get_service().describe_source())
    return JsonResponse({"paused": paused})


@require_POST
def api_seek(request):
    try:
        pos_ms = float(json.loads(request.body or b"{}").get("pos_ms"))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, AttributeError):
        return JsonResponse({"error": "invalid position"}, status=400)
    if pos_ms != pos_ms or pos_ms < 0:   # NaN / negative
        return JsonResponse({"error": "invalid position"}, status=400)
    try:
        get_service().seek(pos_ms)
    except RuntimeError as e:
        return JsonResponse({"error": str(e)}, status=409)
    return JsonResponse({"ok": True})


# Largest real distance accepted for a calibration side, in meters
_MAX_CALIB_M = 500.0


def _calibration_input(body: dict, frame_size):
    """(points, width_m, length_m) from a request body, or raise ValueError with a message for the user."""
    points = body.get("points")
    if not isinstance(points, list) or len(points) != 4:
        raise ValueError("Click exactly 4 points")
    fw, fh = frame_size
    clean = []
    for p in points:
        if not isinstance(p, list) or len(p) != 2:
            raise ValueError("Invalid point")
        x, y = (float(v) for v in p)
        if not (math.isfinite(x) and math.isfinite(y)) or not (0 <= x <= fw and 0 <= y <= fh):
            raise ValueError("Points must be inside the frame")
        clean.append([round(x, 1), round(y, 1)])
    sizes = []
    for key, name in (("width_m", "Width"), ("length_m", "Length")):
        try:
            v = float(body.get(key))
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(v) or not 0 < v <= _MAX_CALIB_M:
            raise ValueError(f"{name} must be between 0 and {_MAX_CALIB_M:.0f} m")
        sizes.append(v)
    return clean, sizes[0], sizes[1]


@require_GET
def api_calibration(request):
    return JsonResponse(get_service().calibration())


@require_GET
def api_calibration_frame(request):
    try:
        jpeg = get_service().calibration_frame()
    except RuntimeError as e:
        return JsonResponse({"error": str(e)}, status=409)
    response = HttpResponse(jpeg, content_type="image/jpeg")
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def api_calibration_save(request):
    service = get_service()
    size = service.frame_size()
    if size is None:
        return JsonResponse({"error": "No video signal to calibrate on"}, status=409)
    try:
        body = json.loads(request.body or b"{}")
        if not isinstance(body, dict):
            raise ValueError("Invalid request")
        points, width_m, length_m = _calibration_input(body, size)
        service.save_calibration(points, width_m, length_m, request.user)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request"}, status=400)
    except ValueError as e:
        msg = str(e)
        if "convex" in msg or "degenerate" in msg:
            msg = "The 4 points must outline a rectangle on the road, clicked in order"
        return JsonResponse({"error": msg}, status=400)
    except RuntimeError as e:
        return JsonResponse({"error": str(e)}, status=409)
    _audit(request, "speed_calibration", f"{service.describe_source()}: {width_m:g} x {length_m:g} m")
    return JsonResponse(service.calibration())


@require_POST
def api_calibration_remove(request):
    service = get_service()
    try:
        service.remove_calibration(request.user)
    except RuntimeError as e:
        return JsonResponse({"error": str(e)}, status=409)
    _audit(request, "speed_calibration_removed", service.describe_source())
    return JsonResponse(service.calibration())


@require_GET
def stream(request):
    """MJPEG stream of the annotated video (or the No Signal card)."""
    service = get_service()
    session = request.session
    interval = 1.0 / settings.STREAM_FPS

    def frames():
        last = None
        next_check = time.time() + 30
        while True:
            # A stream outlives its page; stop it once the session has expired or the user logged out
            if time.time() >= next_check:
                next_check = time.time() + 30
                if not session.exists(session.session_key) or session.get_expiry_date() <= timezone.now():
                    return
            jpeg = service.jpeg()
            if jpeg and jpeg is not last:
                last = jpeg
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
            time.sleep(interval)

    response = StreamingHttpResponse(frames(), content_type="multipart/x-mixed-replace; boundary=frame")
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def snapshot(request, pk: int):
    record = VehicleRecord.objects.filter(pk=pk).only("snapshot").first()
    if record is None or not record.snapshot:
        raise Http404
    media_root = Path(settings.MEDIA_ROOT).resolve()
    path = (media_root / record.snapshot).resolve()
    # Paths come from our own DB, but never serve anything outside MEDIA_ROOT
    if media_root not in path.parents or not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), content_type="image/jpeg")
