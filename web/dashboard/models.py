from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SourceMode(models.TextChoices):
    LIVE = "live", _("Live camera (RTSP)")
    FILE = "file", _("Video file")


class CameraSettings(models.Model):
    # The selected video source (a single row, pk=1); restored when the server restarts.

    mode = models.CharField(max_length=8, choices=SourceMode.choices, default=SourceMode.LIVE)
    # File name inside VIDEO_INPUT_DIR, never a path
    video_file = models.CharField(max_length=255, blank=True)
    # File names played one after another, video_file first; empty = video_file alone
    playlist = models.JSONField(default=list, blank=True)
    # Live mode: '' = IP camera (RTSP_URL), else a key of engine.list_live_sources()
    live_source = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = verbose_name_plural = "Camera settings"

    @classmethod
    def load(cls) -> "CameraSettings":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class SystemSettings(models.Model):
    # Settings Admins change on the settings page (a single row, pk=1).
    # The retention limits start from .env (settings.py); the violation rules start from src/app_config.py.

    speed_limit_kmh = models.PositiveSmallIntegerField(default=80)   # 0 = speed is not a violation
    max_riders = models.PositiveSmallIntegerField(default=2)
    record_retention_days = models.PositiveIntegerField()
    photo_retention_days = models.PositiveIntegerField()
    media_max_mb = models.PositiveIntegerField()
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = verbose_name_plural = "System settings"

    @classmethod
    def load(cls) -> "SystemSettings":
        obj, _created = cls.objects.get_or_create(pk=1, defaults={
            "record_retention_days": settings.RECORD_RETENTION_DAYS,
            "photo_retention_days": settings.PHOTO_RETENTION_DAYS,
            "media_max_mb": settings.MEDIA_MAX_MB,
        })
        return obj


class TableFilter(models.TextChoices):
    ALL = "all", _("All")
    NORMAL = "normal", _("Normal")
    VIOLATION = "violation", _("Violation")


class Units(models.TextChoices):
    # How speeds and distances are shown. They are always stored in metric; the page converts them.
    METRIC = "metric", _("Metric (km/h, m)")
    IMPERIAL = "imperial", _("Imperial (mph, ft)")


class Language(models.TextChoices):
    # Language of the dashboard pages; each name is written in its own language.
    EN = "en", "English"
    TH = "th", "ไทย"


# Choices of UserPreferences.seek_step_s
SEEK_STEPS_S = (5, 10, 15, 30)
# Choices of UserPreferences.stream_fps, offered up to each video file's own rate; the highest stands for
# "the file's own rate" (dashboard.js)
STREAM_FPS_CHOICES = (10, 15, 25, 30, 60)


class UserPreferences(models.Model):
    # How the live view looks and behaves for one staff account.

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences")
    table_filter = models.CharField(max_length=10, choices=TableFilter.choices, default=TableFilter.ALL)
    seek_step_s = models.PositiveSmallIntegerField(default=10)   # << / >> buttons and the arrow keys
    show_fps_chart = models.BooleanField(default=True)
    # Frame rate this account's live view sends a video file at, at most the file's own (live_frames)
    stream_fps = models.PositiveSmallIntegerField(default=25)
    # A pop-up on any dashboard page when a new violation is recorded (notify.js)
    violation_notify = models.BooleanField(default=False)
    # Admins: a pop-up when a live camera goes offline or comes back (notify.js)
    camera_notify = models.BooleanField(default=True)
    units = models.CharField(max_length=8, choices=Units.choices, default=Units.METRIC)
    language = models.CharField(max_length=5, choices=Language.choices, default=Language.EN)

    class Meta:
        verbose_name = verbose_name_plural = "User preferences"

    @classmethod
    def load(cls, user) -> "UserPreferences":
        obj, _created = cls.objects.get_or_create(user=user)
        return obj


class SpeedCalibration(models.Model):
    # Speed calibration of one video source.
    # points: 4 frame-pixel points (far-left, far-right, near-right, near-left); width_m: points 1-2, length_m: 2-3.
    # reference_frame: video files only, the JPEG the points were clicked on. points = null: removed on purpose.

    source_key = models.CharField(max_length=300, unique=True)
    points = models.JSONField(null=True, blank=True)
    width_m = models.FloatField(null=True, blank=True)
    length_m = models.FloatField(null=True, blank=True)
    reference_frame = models.BinaryField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.source_key


class SourceSettings(models.Model):
    # What staff set for one video source in the live view's video settings menu (source_settings.py).
    # A source without a row uses the defaults below.

    source_key = models.CharField(max_length=300, unique=True)   # VideoSource.key, like SpeedCalibration
    # Speeds over this are violations on this source; None = the system's speed limit (SystemSettings)
    speed_limit_kmh = models.PositiveSmallIntegerField(null=True, blank=True)
    # Corners of the area motorcycles are detected in, [[x, y], ...] in frame pixels; None = the whole frame
    detection_zone = models.JSONField(null=True, blank=True)

    # Video files only
    # When the file started recording: its motorcycles are saved at this time plus their time in the clip.
    # None = the time the camera wrote into the file
    recorded_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=100, blank=True)   # where it was filmed, saved with each record
    # That location on the map (picked in the menu); None = not picked
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    # Played again: motorcycles already saved from the same part of the file are not saved a second time
    skip_repeats = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.source_key


class Violation(models.TextChoices):
    NO_HELMET = "NO_HELMET", _("No helmet")
    OVERLOAD = "OVERLOAD", _("Too many riders")
    SPEEDING = "SPEEDING", _("Speeding")


class Review(models.TextChoices):
    # An admin's verdict on a recorded event, set from the plate search page.
    PENDING = "pending", _("Not reviewed")
    CONFIRMED = "confirmed", _("Confirmed")
    REJECTED = "rejected", _("Rejected")


class VehicleRecord(models.Model):
    # A motorcycle that passed the camera (one row per finished track).

    detected_at = models.DateTimeField(db_index=True)
    # When the row was written; differs from detected_at for video files recorded earlier, and is what the
    # storage clean-up counts retention from (so a clip filmed months ago is not deleted as soon as it is saved)
    saved_at = models.DateTimeField(default=timezone.now, db_index=True)
    source_mode = models.CharField(max_length=8, choices=SourceMode.choices)
    source_name = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=100, blank=True)   # where a video file was filmed (SourceSettings)
    track_id = models.PositiveIntegerField()
    # Video files: when the motorcycle came into and left view, in seconds into the clip, to know it again
    # when the file is played again (recorder.py)
    clip_start_s = models.FloatField(null=True, blank=True)
    clip_end_s = models.FloatField(null=True, blank=True)

    plate_text = models.CharField(max_length=32, blank=True)
    province_text = models.CharField(max_length=64, blank=True)
    plate_conf = models.FloatField(null=True, blank=True)

    riders = models.PositiveSmallIntegerField(null=True, blank=True)
    helmet_count = models.PositiveSmallIntegerField(null=True, blank=True)
    no_helmet_count = models.PositiveSmallIntegerField(null=True, blank=True)
    speed_kmh = models.FloatField(null=True, blank=True)

    # Comma-separated Violation codes; empty for a normal vehicle
    violations = models.CharField(max_length=64, blank=True)
    is_violation = models.BooleanField(default=False, db_index=True)
    # Path relative to MEDIA_ROOT
    snapshot = models.CharField(max_length=255, blank=True)
    # Sharpest crop of the licence plate, when one was found (path relative to MEDIA_ROOT)
    plate_snapshot = models.CharField(max_length=255, blank=True)

    review = models.CharField(max_length=10, choices=Review.choices, default=Review.PENDING, db_index=True)
    review_note = models.CharField(max_length=255, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["source_name", "clip_end_s"])]

    def __str__(self):
        return f"#{self.track_id} {self.plate_text or '-'} @ {self.detected_at:%Y-%m-%d %H:%M:%S}"

    @property
    def violation_labels(self) -> list:
        return [Violation(code).label for code in self.violations.split(",") if code in Violation.values]


class AuditLog(models.Model):
    # Who changed what from the dashboard, and from where.

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=64)
    detail = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.user} {self.action}"


class Access(models.Model):
    # No table: only holds the permissions that open the dashboard's own pages.
    # They are given to groups (roles) in Django admin and checked with user.has_perm() in middleware.py.

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("use_dashboard", "Can use the live view, change the video source and review events"),
            ("view_records", "Can search records and view violation statistics"),
            ("view_system_status", "Can view camera and system status"),
            ("manage_calibration", "Can change the distances of, or remove, saved speed calibrations"),
            ("delete_records", "Can delete vehicle records and their evidence photos"),
            ("manage_settings", "Can change the violation rules and how long records are kept"),
        ]
