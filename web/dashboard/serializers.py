"""Shapes of the dashboard API's requests and responses.

Request serializers check what the browser sends before the views act on it; their error messages are
shown to the admin as they are, so they are written for people.
"""
from django.utils import timezone
from rest_framework import serializers

from province import THAI_PROVINCES

from .models import Review, SourceMode
from .plate_search import MIN_QUERY_LENGTH, normalize_query

# Largest real distance accepted for a calibration side, in meters
MAX_CALIBRATION_M = 500.0


# ---------- requests ----------

class VehicleQuerySerializer(serializers.Serializer):
    """?after=<id>: only rows newer than this id; 0 (default) means the latest rows."""
    after = serializers.IntegerField(min_value=0, default=0)


class PlateSearchSerializer(serializers.Serializer):
    """?q=<plate or part of it>&province=<part of a name>&violations_only=true
    An empty q lists every record, newest first, a page at a time (&before=<id of the last row shown>)."""
    q = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    # Thai name, as stored on the records; the page lists them by their English names
    province = serializers.ChoiceField(choices=THAI_PROVINCES, required=False, allow_blank=True, default="",
                                       error_messages={"invalid_choice": "Unknown province"})
    violations_only = serializers.BooleanField(default=False)
    before = serializers.IntegerField(min_value=1, required=False, default=None)

    def validate_q(self, q):
        q = q.strip()
        if q and len(normalize_query(q)) < MIN_QUERY_LENGTH:
            raise serializers.ValidationError(f"Type at least {MIN_QUERY_LENGTH} plate characters")
        return q


class VehicleDeleteSerializer(PlateSearchSerializer):
    """Delete the records in ids, or (all=true) every record the list with these filters shows."""
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list,
                                max_length=5000)
    all = serializers.BooleanField(default=False)

    def validate(self, data):
        if not data["all"] and not data["ids"]:
            raise serializers.ValidationError("Select the records to delete")
        return data


class ReviewSerializer(serializers.Serializer):
    """Confirm or reject a recorded event; "pending" undoes the review."""
    review = serializers.ChoiceField(choices=Review.choices)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


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


class SourceKeySerializer(serializers.Serializer):
    """A video source on the system status page, e.g. "live:ip", "live:itic-ccs06", "file:clip.mp4"."""
    source_key = serializers.CharField(max_length=300)

    def validate_source_key(self, key):
        from .engine import source_keys

        if key not in source_keys():
            raise serializers.ValidationError("Unknown video source")
        return key


class SourceDistancesSerializer(SourceKeySerializer):
    """New real width and length of a source's saved calibration rectangle (its points stay)."""
    width_m = _size_field("Width")
    length_m = _size_field("Length")


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
    review = serializers.CharField()
    review_label = serializers.CharField(source="get_review_display")
    review_note = serializers.CharField()
    reviewed_by = serializers.SerializerMethodField()
    reviewed_at = serializers.SerializerMethodField()

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

    def get_reviewed_by(self, record) -> str:
        return record.reviewed_by.get_username() if record.reviewed_by else ""

    def get_reviewed_at(self, record) -> str:
        return timezone.localtime(record.reviewed_at).strftime("%d/%m/%Y %H:%M") if record.reviewed_at else ""
