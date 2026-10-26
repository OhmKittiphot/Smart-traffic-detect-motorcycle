"""The live view: its video stream, the source it shows and playback, and the table of vehicles passing."""
import time

from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import storage
from ..engine import get_service
from ..models import CameraSettings, VehicleRecord
from ..serializers import PauseSerializer, SeekSerializer, SourceSerializer, VehicleQuerySerializer, VehicleRecordSerializer
from ..sources import VideoSource
from .common import audit, bad_request, conflict

# Rows sent on first load / per poll
_INITIAL_ROWS = 100
_MAX_NEW_ROWS = 200


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


class VehicleClearView(APIView):
    def post(self, request):
        """Delete every vehicle record and its evidence snapshot (live view; needs delete_records)."""
        deleted = storage.delete_records(VehicleRecord.objects.values_list("id", flat=True))
        audit(request, "clear_vehicles", f"{deleted} records")
        return Response({"deleted": deleted})


class SourceView(APIView):
    def post(self, request):
        """Switch to a video file or a live camera, and remember the choice for the next start."""
        serializer = SourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        source = VideoSource(data["mode"], data["file"], data["live"])

        service = get_service()
        try:
            service.switch(source)
        except ValueError as e:
            return bad_request(e)
        except RuntimeError as e:
            return conflict(e)

        saved = CameraSettings.load()
        saved.mode, saved.video_file, saved.live_source = source.mode, source.video_file, source.live_key
        saved.updated_by = request.user
        saved.save()
        audit(request, "switch_source", source.describe())
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
            return conflict(e)
        audit(request, "pause" if paused else "resume", service.source.describe())
        return Response({"paused": paused})


class SeekView(APIView):
    def post(self, request):
        """Jump to a position in the video file."""
        serializer = SeekSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            get_service().seek(serializer.validated_data["pos_ms"])
        except RuntimeError as e:
            return conflict(e)
        return Response({"ok": True})


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
            jpeg = get_service(viewer).frames.jpeg()
            # An unchanged frame (paused video) is sent again every second: the server only notices a
            # closed page when it writes, and a stream that never writes would hold its thread forever
            if jpeg and (jpeg is not last or time.time() - last_sent_t >= 1.0):
                last, last_sent_t = jpeg, time.time()
                yield jpeg + b"\r\n" + part_head
            time.sleep(interval)

    response = StreamingHttpResponse(frames(), content_type="multipart/x-mixed-replace; boundary=frame")
    response["Cache-Control"] = "no-store"
    return response
