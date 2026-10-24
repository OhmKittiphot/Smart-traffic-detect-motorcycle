from django.conf import settings
from django.db import models


class SourceMode(models.TextChoices):
    LIVE = "live", "Live camera (RTSP)"
    FILE = "file", "Video file"


class CameraSettings(models.Model):
    """The selected video source (a single row, pk=1); restored when the server restarts."""

    mode = models.CharField(max_length=8, choices=SourceMode.choices, default=SourceMode.LIVE)
    # File name inside VIDEO_INPUT_DIR, never a path
    video_file = models.CharField(max_length=255, blank=True)
    # Live mode: '' = IP camera (RTSP_URL), else a key of engine.list_live_sources()
    live_source = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = verbose_name_plural = "Camera settings"

    @classmethod
    def load(cls) -> "CameraSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SpeedCalibration(models.Model):
    """Ground-plane speed calibration of one video source (each camera view needs its own).

    points: 4 image points in frame pixels, order far-left, far-right, near-right, near-left.
    width_m: real distance between points 1-2, length_m: between points 2-3.
    A row with points = null means the calibration was removed on purpose."""

    source_key = models.CharField(max_length=300, unique=True)
    points = models.JSONField(null=True, blank=True)
    width_m = models.FloatField(null=True, blank=True)
    length_m = models.FloatField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.source_key


class Violation(models.TextChoices):
    NO_HELMET = "NO_HELMET", "No helmet"
    OVERLOAD = "OVERLOAD", "Too many riders"
    SPEEDING = "SPEEDING", "Speeding"


class VehicleRecord(models.Model):
    """A motorcycle that passed the camera (one row per finished track)."""

    detected_at = models.DateTimeField(db_index=True)
    source_mode = models.CharField(max_length=8, choices=SourceMode.choices)
    source_name = models.CharField(max_length=255, blank=True)
    track_id = models.PositiveIntegerField()

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

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"#{self.track_id} {self.plate_text or '-'} @ {self.detected_at:%Y-%m-%d %H:%M:%S}"

    @property
    def violation_labels(self) -> list:
        return [Violation(code).label for code in self.violations.split(",") if code in Violation.values]


class AuditLog(models.Model):
    """Who changed what from the dashboard, and from where."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=64)
    detail = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.user} {self.action}"
