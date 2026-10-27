"""Shapes of the dashboard API's requests and responses.

Request serializers check what the browser sends before the views act on it; their error messages are
shown to the admin as they are, so they are written for people.
"""
from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from province import THAI_PROVINCES

from .models import SEEK_STEPS_S, Review, SourceMode, SystemSettings, TableFilter, Units, UserPreferences
from .plate_search import MAX_QUERIES, MIN_QUERY_LENGTH, normalize_query, split_queries
from .sources import list_video_files

# Largest real distance accepted for a calibration side, in meters
MAX_CALIBRATION_M = 500.0


# ---------- requests ----------

def _known_source(source):
    """A source filter: "<source_mode>:<source_name>" ("file:clip.mp4", "live:" = IP camera) is one source,
    "<source_mode>" alone ("live" = every live camera, "file" = every video file) a whole group."""
    if source and source.partition(":")[0] not in SourceMode.values:
        raise serializers.ValidationError(_("Unknown video source"))

class RecordEventsSerializer(serializers.Serializer):
    """?after=<id> (or the Last-Event-ID header): records newer than this id; none means from now on."""
    after = serializers.IntegerField(min_value=0, required=False, allow_null=True, default=None)


class StatsQuerySerializer(serializers.Serializer):
    """?start=<YYYY-MM-DD>&end=<YYYY-MM-DD>: the days the violation statistics cover, both included.
    Without them, the last DEFAULT_DAYS days, today included.
    &month=<YYYY-MM>: the month the calendar shows, this month by default (given as its first day).
    &source=<mode>:<name> or <mode>: only the records of that video source (see _known_source), all by default."""
    DEFAULT_DAYS = 30
    MAX_DAYS = 366
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    month = serializers.DateField(input_formats=["%Y-%m"], required=False)
    source = serializers.CharField(max_length=300, required=False, allow_blank=True, default="",
                                   validators=[_known_source])

    def validate(self, data):
        today = timezone.localdate()
        end = data.get("end") or today
        start = data.get("start") or end - timedelta(days=self.DEFAULT_DAYS - 1)
        if start > end:
            raise serializers.ValidationError(_("The start date is after the end date"))
        if (end - start).days >= self.MAX_DAYS:
            raise serializers.ValidationError(_("Choose at most %(days)s days") % {"days": self.MAX_DAYS})
        month = data.get("month") or today.replace(day=1)
        if month > today:
            raise serializers.ValidationError(_("That month has not started yet"))
        return {"start": start, "end": end, "month": month, "source": data["source"]}


class PlateSearchSerializer(serializers.Serializer):
    """?q=<plate or part of it, or several separated by commas>&province=<Thai name>&source=<mode>:<name>
    &violations_only=true
    An empty q with browse=true (the violations page) lists every record, newest first, a page at a time
    (&before=<id of the last row shown>); without browse it lists nothing until a plate is typed."""
    q = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    # Thai name, as stored on the records; the page lists them by their English names
    province = serializers.ChoiceField(choices=THAI_PROVINCES, required=False, allow_blank=True, default="",
                                       error_messages={"invalid_choice": _("Unknown province")})
    # Where the records came from (see _known_source)
    source = serializers.CharField(max_length=300, required=False, allow_blank=True, default="",
                                   validators=[_known_source])
    violations_only = serializers.BooleanField(default=False)
    browse = serializers.BooleanField(default=False)
    before = serializers.IntegerField(min_value=1, required=False, default=None)

    def validate_q(self, q):
        plates = split_queries(q)
        if len(plates) > MAX_QUERIES:
            raise serializers.ValidationError(_("Search at most %(n)s plates at once") % {"n": MAX_QUERIES})
        for plate in plates:
            if len(normalize_query(plate)) < MIN_QUERY_LENGTH:
                raise serializers.ValidationError(
                    _("Type at least %(n)s plate characters for each plate (“%(plate)s” is too short)")
                    % {"n": MIN_QUERY_LENGTH, "plate": plate})
        return ", ".join(plates)


class VehicleDeleteSerializer(PlateSearchSerializer):
    """Delete the records in ids, or (all=true) every record the list with these filters shows."""
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list,
                                max_length=5000)
    all = serializers.BooleanField(default=False)

    def validate(self, data):
        if not data["all"] and not data["ids"]:
            raise serializers.ValidationError(_("Select the records to delete"))
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
    # Mode "file": video files to play one after another, in this order, instead of "file" alone
    playlist = serializers.ListField(child=serializers.CharField(max_length=255), required=False, default=list,
                                     max_length=500)

    def validate(self, data):
        # Only the choice that belongs to the selected mode is kept
        if data["mode"] == SourceMode.LIVE:
            data["file"], data["playlist"] = "", []
            return data
        data["live"] = ""
        playlist = list(dict.fromkeys(data["playlist"]))   # each file once
        if not set(playlist) <= set(list_video_files()):
            raise serializers.ValidationError(_("A file of the playlist was not found"))
        if playlist:
            data["file"] = playlist[0]
        # A single file is not a playlist
        data["playlist"] = playlist if len(playlist) > 1 else []
        return data


class PauseSerializer(serializers.Serializer):
    paused = serializers.BooleanField(required=False)   # left out: flip pause


class SeekSerializer(serializers.Serializer):
    pos_ms = serializers.FloatField(min_value=0)   # position in the video, in milliseconds


class WebRTCOfferSerializer(serializers.Serializer):
    sdp = serializers.CharField(max_length=20000)   # the browser's session description (a few KB)


def _size_field(name):
    message = format_lazy(_("{name} must be between 0.1 and {max} m"), name=name, max=f"{MAX_CALIBRATION_M:.0f}")
    return serializers.FloatField(min_value=0.1, max_value=MAX_CALIBRATION_M, error_messages={
        "required": format_lazy(_("{name} is required"), name=name),
        "invalid": format_lazy(_("{name} must be a number"), name=name),
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
        error_messages={"min_length": _("Click exactly 4 points"), "max_length": _("Click exactly 4 points")},
    )
    width_m = _size_field(_("Width"))
    length_m = _size_field(_("Length"))

    def validate_points(self, points):
        frame_w, frame_h = self.context["frame_size"]
        for x, y in points:
            if not (0 <= x <= frame_w and 0 <= y <= frame_h):
                raise serializers.ValidationError(_("Points must be inside the frame"))
        return [[round(x, 1), round(y, 1)] for x, y in points]


class SourceKeySerializer(serializers.Serializer):
    """A video source on the system status page, e.g. "live:ip", "live:itic-ccs06", "file:clip.mp4"."""
    source_key = serializers.CharField(max_length=300)

    def validate_source_key(self, key):
        from .sources import source_keys

        if key not in source_keys():
            raise serializers.ValidationError(_("Unknown video source"))
        return key


class SourceDistancesSerializer(SourceKeySerializer):
    """New real width and length of a source's saved calibration rectangle (its points stay)."""
    width_m = _size_field(_("Width"))
    length_m = _size_field(_("Length"))


def _whole_field(name, low, high, unit):
    message = format_lazy(_("{name} must be a whole number from {low} to {high} {unit}"),
                          name=name, low=low, high=high, unit=unit)
    return serializers.IntegerField(min_value=low, max_value=high, error_messages={
        "required": format_lazy(_("{name} is required"), name=name),
        "invalid": message,
        "min_value": message,
        "max_value": message,
    })


class SystemSettingsSerializer(serializers.ModelSerializer):
    """Admin: violation rules and how long records and photos are kept."""
    speed_limit_kmh = _whole_field(_("Speed limit"), 0, 300, "km/h")
    max_riders = _whole_field(_("Riders allowed"), 1, 10, _("people"))
    record_retention_days = _whole_field(_("Keep records for"), 1, 3650, _("days"))
    photo_retention_days = _whole_field(_("Keep photos for"), 1, 3650, _("days"))
    media_max_mb = _whole_field(_("Photo space"), 100, 1_000_000, "MB")

    class Meta:
        model = SystemSettings
        fields = ["speed_limit_kmh", "max_riders", "record_retention_days", "photo_retention_days", "media_max_mb"]

    def validate(self, data):
        if data["photo_retention_days"] > data["record_retention_days"]:
            raise serializers.ValidationError(_("Photos cannot be kept longer than the records they belong to"))
        return data


class PreferencesSerializer(serializers.ModelSerializer):
    """Staff: how the live view looks and behaves for this account."""
    table_filter = serializers.ChoiceField(choices=TableFilter.choices)
    seek_step_s = serializers.ChoiceField(choices=SEEK_STEPS_S,
                                          error_messages={"invalid_choice": _("Choose one of the skip lengths")})
    units = serializers.ChoiceField(choices=Units.choices)

    class Meta:
        model = UserPreferences
        fields = ["table_filter", "seek_step_s", "show_fps_chart", "violation_notify", "camera_notify", "units",
                  "language"]


class PasswordChangeSerializer(serializers.Serializer):
    """The view passes the logged-in user as context["user"]."""
    old_password = serializers.CharField(error_messages={"blank": _("Type your current password")})
    new_password = serializers.CharField(error_messages={"blank": _("Type a new password")})
    confirm_password = serializers.CharField(error_messages={"blank": _("Type the new password again")})

    def validate_old_password(self, password):
        if not self.context["user"].check_password(password):
            raise serializers.ValidationError(_("The current password is wrong"))
        return password

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": [_("The two new passwords do not match")]})
        try:
            validate_password(data["new_password"], self.context["user"])
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password": e.messages})
        return data


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
    plate_snapshot = serializers.SerializerMethodField()   # True when a photo of the plate can be opened
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

    def get_plate_snapshot(self, record) -> bool:
        return bool(record.plate_snapshot)

    def get_reviewed_by(self, record) -> str:
        return record.reviewed_by.get_username() if record.reviewed_by else ""

    def get_reviewed_at(self, record) -> str:
        return timezone.localtime(record.reviewed_at).strftime("%d/%m/%Y %H:%M") if record.reviewed_at else ""
