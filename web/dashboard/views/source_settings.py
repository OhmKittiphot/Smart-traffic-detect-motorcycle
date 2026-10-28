"""The live view's video settings menu: the settings of the source it shows (source_settings.py). Its speed
calibration has views of its own (calibration.py); the frame rate is each account's preference
(settings.py)."""
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import SourceSettings, VehicleRecord
from ..serializers import DetectionZoneSerializer, SourceSettingsSerializer
from ..source_settings import recording_start
from .common import audit, conflict, running_service


def _settings_data(service) -> dict:
    source, settings = service.source, service.settings
    data = {
        "source_key": source.key,
        "source": source.describe(),
        "is_file": source.is_file,
        "speed_calibrated": service.app.speed_estimator.calibrated,
        "speed_limit_kmh": settings.speed_limit_kmh,   # None: the system's
        "system_speed_limit_kmh": service.config.speed_limit_kmh,
        "detection_zone": settings.detection_zone,
    }
    if source.is_file:
        recorded_at, recorded_from = recording_start(source.video_file, settings)
        data.update({
            # In the server's local time, which the menu's date and time field shows as it is
            "recorded_at": timezone.localtime(recorded_at).isoformat() if recorded_at else None,
            "recorded_from": recorded_from,
            "location": settings.location,
            "latitude": settings.latitude,
            "longitude": settings.longitude,
            "skip_repeats": settings.skip_repeats,
        })
    return data


def _changing_source(request, service):
    """The settings are for the source the menu was opened on: None, else a 409 when another tab has
    switched the source meanwhile."""
    if request.data.get("source_key") != service.source.key:
        return conflict(_("The video source has changed; open the video settings again"))
    return None


def _save(service, user, **fields) -> None:
    settings, _created = SourceSettings.objects.get_or_create(source_key=service.source.key)
    for name, value in fields.items():
        setattr(settings, name, value)
    settings.updated_by = user
    settings.save()
    service.reload_settings(settings.source_key)


class SourceSettingsView(APIView):
    def get(self, request):
        """The settings of the source the live view shows."""
        return Response(_settings_data(running_service()))

    def post(self, request):
        """Change some of them (the fields sent); the others are kept."""
        service = running_service()
        changed = _changing_source(request, service)
        if changed:
            return changed
        serializer = SourceSettingsSerializer(data=request.data, context={"is_file": service.source.is_file})
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data
        _save(service, request.user, **fields)
        if "location" in fields:
            # Where a file was filmed does not change: its motorcycles saved before get the location too
            VehicleRecord.objects.filter(source_mode="file", source_name=service.source.video_file) \
                .update(location=fields["location"])
        audit(request, "source_settings",
              f"{service.source.describe()}: " + ", ".join(f"{name}={value}" for name, value in fields.items()))
        return Response(_settings_data(service))


class DetectionZoneView(APIView):
    def post(self, request):
        """Save the area motorcycles are detected in, clicked on the current frame; points null = the whole
        frame. Applies at once."""
        service = running_service()
        changed = _changing_source(request, service)
        if changed:
            return changed
        frame_size = service.frames.size()
        if frame_size is None:
            return conflict(_("No video signal to draw on"))
        serializer = DetectionZoneSerializer(data=request.data, context={"frame_size": frame_size})
        serializer.is_valid(raise_exception=True)
        points = serializer.validated_data["points"]
        _save(service, request.user, detection_zone=points)
        audit(request, "detection_zone",
              f"{service.source.describe()}: " + (f"{len(points)} points" if points else "the whole frame"))
        return Response(_settings_data(service))
