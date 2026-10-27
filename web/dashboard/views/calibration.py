"""Speed calibration: 4 points of a rectangle on the road and its real size, saved per video source.

Staff set it on the source the live view is showing (Calibration*View); Admins change or redraw the saved
calibration of any source from the system status page (SourceCalibration*View), with detection on or off.
"""
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import calibrations
from ..engine import current_service
from ..live_frames import encode_jpeg
from ..models import SpeedCalibration
from ..serializers import CalibrationSerializer, SourceDistancesSerializer, SourceKeySerializer
from ..sources import grab_frame
from .common import audit, conflict, jpeg_response, running_service

_NOT_A_RECTANGLE = {"points": [gettext_lazy("The 4 points must outline a rectangle on the road, clicked in order")]}
_REDRAW_FRAME_KEEP_S = 60 * 60


# Cached per user and source, so two people editing one source each save the frame they clicked on

def _frame_size_key(request, source_key) -> str:
    """Size of the last frame sent for redrawing a source's points, so the points saved for it can be checked."""
    return f"calibration-frame-size:{request.user.pk}:{source_key}"


def _clicked_frame_key(request, source_key) -> str:
    """The last frame sent to click a source's points on (JPEG): a video file keeps it with its calibration,
    so the points can follow a moving camera."""
    return f"calibration-frame:{request.user.pk}:{source_key}"


# ---------- current source (live view) ----------

class CalibrationView(APIView):
    def get(self, request):
        """Speed calibration of the current source (points in frame pixels)."""
        return Response(running_service().calibration())


class CalibrationFrameView(APIView):
    def get(self, request):
        """The current frame without overlays, as JPEG, for clicking the calibration points on."""
        service = running_service()
        try:
            jpeg = service.frames.raw_jpeg()
        except RuntimeError as e:
            return conflict(e)
        cache.set(_clicked_frame_key(request, service.source.key), jpeg, _REDRAW_FRAME_KEEP_S)
        return jpeg_response(jpeg)


class CalibrationSaveView(APIView):
    def post(self, request):
        """Save the 4 points and real size clicked on the current source's frame."""
        service = running_service()
        frame_size = service.frames.size()
        if frame_size is None:
            return conflict(_("No video signal to calibrate on"))

        serializer = CalibrationSerializer(data=request.data, context={"frame_size": frame_size})
        serializer.is_valid(raise_exception=True)
        points, width_m, length_m = (serializer.validated_data[f] for f in ("points", "width_m", "length_m"))
        try:
            service.save_calibration(points, width_m, length_m, request.user,
                                     cache.get(_clicked_frame_key(request, service.source.key)))
        except ValueError:
            # The speed estimator rejects shapes that are not a convex 4-sided outline
            return Response(_NOT_A_RECTANGLE, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return conflict(e)
        audit(request, "speed_calibration", f"{service.source.describe()}: {width_m:g} x {length_m:g} m")
        return Response(service.calibration())


class CalibrationRemoveView(APIView):
    def post(self, request):
        """Remove the speed calibration of the current source (speeds are no longer measured)."""
        service = running_service()
        try:
            service.remove_calibration(request.user)
        except RuntimeError as e:
            return conflict(e)
        audit(request, "speed_calibration_removed", service.source.describe())
        return Response(service.calibration())


# ---------- any source (system status page) ----------

def _reload_calibration(source_key):
    """Apply a calibration changed on the system status page, when detection is on (it loads it on start)."""
    service = current_service()
    if service is not None:
        service.reload_calibration(source_key)


class SourceCalibrationView(APIView):
    def post(self, request):
        """Admin: change the real width and length of a source's saved calibration (not its points)."""
        serializer = SourceDistancesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key, width_m, length_m = (serializer.validated_data[f] for f in ("source_key", "width_m", "length_m"))
        updated = (SpeedCalibration.objects.filter(source_key=key, points__isnull=False)
                   .update(width_m=width_m, length_m=length_m, updated_by=request.user, updated_at=timezone.now()))
        if not updated:
            return conflict(_("This source has no saved calibration; staff set the points from the live view"))
        _reload_calibration(key)
        audit(request, "speed_calibration", f"{key}: {width_m:g} x {length_m:g} m")
        return Response({"source_key": key, "width_m": width_m, "length_m": length_m})


class SourceCalibrationFrameView(APIView):
    def get(self, request):
        """Admin: a current frame of a source to redraw its points on. Detection need not be on: the
        source is opened on its own unless detection is already showing it."""
        serializer = SourceKeySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["source_key"]
        service = current_service()
        try:
            if (service is not None and service.state == service.RUNNING and service.source.key == key
                    and service.has_signal()):
                jpeg, size = service.frames.raw_jpeg(), service.frames.size()
            else:
                frame = grab_frame(key)
                jpeg, size = encode_jpeg(frame, quality=90), (frame.shape[1], frame.shape[0])
                if not jpeg:
                    raise RuntimeError(_("Could not capture a frame"))
        except RuntimeError as e:
            return conflict(e)
        cache.set(_frame_size_key(request, key), size, _REDRAW_FRAME_KEEP_S)
        cache.set(_clicked_frame_key(request, key), jpeg, _REDRAW_FRAME_KEEP_S)
        return jpeg_response(jpeg)


class SourceCalibrationPointsView(APIView):
    def post(self, request):
        """Admin: save the 4 points (and real size) redrawn on the frame from SourceCalibrationFrameView."""
        from speed import SpeedEstimator

        source = SourceKeySerializer(data=request.data)
        source.is_valid(raise_exception=True)
        key = source.validated_data["source_key"]
        frame_size = cache.get(_frame_size_key(request, key))
        if frame_size is None:
            return conflict(_("The frame expired; open the editor again"))

        serializer = CalibrationSerializer(data=request.data, context={"frame_size": frame_size})
        serializer.is_valid(raise_exception=True)
        points, width_m, length_m = (serializer.validated_data[f] for f in ("points", "width_m", "length_m"))
        try:
            # Same shape check detection applies (convex, in order, not tiny)
            SpeedEstimator().set_calibration([tuple(p) for p in points], (width_m, length_m))
        except ValueError:
            return Response(_NOT_A_RECTANGLE, status=status.HTTP_400_BAD_REQUEST)
        calibrations.save(key, points, width_m, length_m, request.user, cache.get(_clicked_frame_key(request, key)))
        _reload_calibration(key)
        audit(request, "speed_calibration", f"{key}: {width_m:g} x {length_m:g} m (points redrawn)")
        return Response({"source_key": key, "points": points, "width_m": width_m, "length_m": length_m})


class SourceCalibrationRemoveView(APIView):
    def post(self, request):
        """Admin: remove a source's speed calibration (speeds are no longer measured on it)."""
        serializer = SourceKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["source_key"]
        calibrations.remove(key, request.user)
        _reload_calibration(key)
        audit(request, "speed_calibration_removed", key)
        return Response({"source_key": key})
