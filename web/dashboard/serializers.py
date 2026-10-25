"""Shapes of the dashboard API's requests and responses.

Request serializers check what the browser sends before the views act on it; their error messages are
shown to the admin as they are, so they are written for people.
"""
from django.utils import timezone
from rest_framework import serializers

from .models import SourceMode

# Largest real distance accepted for a calibration side, in meters
MAX_CALIBRATION_M = 500.0


# ---------- requests ----------

class VehicleQuerySerializer(serializers.Serializer):
    """?after=<id>: only rows newer than this id; 0 (default) means the latest rows."""
    after = serializers.IntegerField(min_value=0, default=0)


class SourceSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=SourceMode.choices)
    # Video file name inside input/ (mode "file"), or add-on camera key (mode "live", empty = IP camera)
    file = serializers.CharField(required=False, allow_blank=True, default="")
    live = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        # Only the choice that belongs to the selected mode is kept
        if data["mode"] == SourceMode.LIVE:
            data["file"] = ""
        else:
            data["live"] = ""
        return data


class SeekSerializer(serializers.Serializer):
    pos_ms = serializers.FloatField(min_value=0)   # position in the video, in milliseconds


def _size_field(name):
    message = f"{name} must be between 0.1 and {MAX_CALIBRATION_M:.0f} m"
    return serializers.FloatField(min_value=0.1, max_value=MAX_CALIBRATION_M, error_messages={
        "required": f"{name} is required",
        "invalid": f"{name} must be a number",
        "min_value": message,
        "max_value": message,
    })


class CalibrationSerializer(serializers.Serializer):
    """4 corners of a rectangle on the road (frame pixels) and its real width and length.

    The view passes the current frame size as context["frame_size"] = (width, height).
    """
    points = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2),
        min_length=4, max_length=4,
        error_messages={"min_length": "Click exactly 4 points", "max_length": "Click exactly 4 points"},
    )
    width_m = _size_field("Width")
    length_m = _size_field("Length")

    def validate_points(self, points):
        frame_w, frame_h = self.context["frame_size"]
        for x, y in points:
            if not (0 <= x <= frame_w and 0 <= y <= frame_h):
                raise serializers.ValidationError("Points must be inside the frame")
        return [[round(x, 1), round(y, 1)] for x, y in points]


# ---------- responses ----------

class VehicleRecordSerializer(serializers.Serializer):
    """One row of the vehicle table."""
    id = serializers.IntegerField()
    time = serializers.SerializerMethodField()
    date = serializers.SerializerMethodField()
    track_id = serializers.IntegerField()
    plate = serializers.CharField(source="plate_text")
    province = serializers.CharField(source="province_text")
    plate_conf = serializers.SerializerMethodField()   # OCR confidence in percent
    riders = serializers.IntegerField(allow_null=True)
    helmet = serializers.IntegerField(source="helmet_count", allow_null=True)
    no_helmet = serializers.IntegerField(source="no_helmet_count", allow_null=True)
    speed = serializers.FloatField(source="speed_kmh", allow_null=True)
    violation = serializers.BooleanField(source="is_violation")
    violations = serializers.ListField(child=serializers.CharField(), source="violation_labels")
    source = serializers.SerializerMethodField()
    snapshot = serializers.SerializerMethodField()   # True when an evidence photo can be opened

    def get_time(self, record) -> str:
        return timezone.localtime(record.detected_at).strftime("%H:%M:%S")

    def get_date(self, record) -> str:
        return timezone.localtime(record.detected_at).strftime("%d/%m/%Y")

    def get_plate_conf(self, record) -> int | None:
        return round(record.plate_conf * 100) if record.plate_conf else None

    def get_source(self, record) -> str:
        return record.source_name or record.get_source_mode_display()

    def get_snapshot(self, record) -> bool:
        return bool(record.snapshot)
