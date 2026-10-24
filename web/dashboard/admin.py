from django.contrib import admin

from .models import AuditLog, CameraSettings, SpeedCalibration, VehicleRecord


@admin.register(VehicleRecord)
class VehicleRecordAdmin(admin.ModelAdmin):
    list_display = ("detected_at", "track_id", "plate_text", "province_text", "riders", "speed_kmh",
                    "is_violation", "violations", "source_name")
    list_filter = ("is_violation", "source_mode", "detected_at")
    search_fields = ("plate_text", "province_text")
    date_hierarchy = "detected_at"

    # Detection results are evidence: viewable and deletable, but not editable
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "detail", "ip_address")
    list_filter = ("action",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CameraSettings)
class CameraSettingsAdmin(admin.ModelAdmin):
    list_display = ("mode", "video_file", "live_source", "updated_at", "updated_by")

    def has_add_permission(self, request):
        return False


@admin.register(SpeedCalibration)
class SpeedCalibrationAdmin(admin.ModelAdmin):
    list_display = ("source_key", "width_m", "length_m", "updated_at", "updated_by")
    readonly_fields = ("updated_at", "updated_by")
