import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .engine import get_service, list_live_sources, list_video_files
from .models import AuditLog, CameraSettings, VehicleRecord
from .serializers import (CalibrationSerializer, SeekSerializer, SourceSerializer, VehicleQuerySerializer,
                          VehicleRecordSerializer)

# Rows sent on first load / per poll
_INITIAL_ROWS = 100
_MAX_NEW_ROWS = 200


def _client_ip(request):
    return request.META.get("REMOTE_ADDR")


def _audit(request, action, detail=""):
    AuditLog.objects.create(user=request.user, action=action, detail=detail[:255], ip_address=_client_ip(request))


def _conflict(error):
    """409: the request is valid, but the video engine cannot do it right now (not ready, wrong mode...)."""
    return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)


# ---------- pages ----------

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


# ---------- API used by the dashboard page (Django REST Framework) ----------
# Errors are JSON: {"detail": "..."} for a whole request, or {"<field>": ["..."]} from a serializer.

class StatusView(APIView):
    def get(self, request):
        """Engine state, playback position and today's vehicle counts (polled every second)."""
        data = get_service().status()
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

        records = VehicleRecord.objects.all()
        if after > 0:
            records = records.filter(id__gt=after)[:_MAX_NEW_ROWS]
        else:
            records = records[:_INITIAL_ROWS]
        return Response({"rows": VehicleRecordSerializer(records, many=True).data})


class VehicleClearView(APIView):
    def post(self, request):
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
        return Response({"deleted": len(records)})


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
        """Pause or resume the video file."""
        service = get_service()
        try:
            paused = service.toggle_pause()
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


class CalibrationSaveView(APIView):
    def post(self, request):
        """Save the speed calibration of the current source."""
        service = get_service()
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


# ---------- video and images (not JSON, so plain Django views) ----------

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
