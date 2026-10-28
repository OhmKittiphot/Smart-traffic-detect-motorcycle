from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import AuditLog, CameraSettings, SpeedCalibration, VehicleRecord


@admin.register(VehicleRecord)
class VehicleRecordAdmin(admin.ModelAdmin):
    list_display = ("detected_at", "track_id", "plate_text", "province_text", "riders", "speed_kmh",
                    "is_violation", "violations", "review", "reviewed_by", "source_name", "location")
    list_filter = ("is_violation", "review", "source_mode", "location", "detected_at")
    search_fields = ("plate_text", "province_text", "location")
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


admin.site.unregister(User)


@admin.register(User)
class RoleUserAdmin(UserAdmin):
    """Admins manage accounts and roles (groups), but may not make anyone a superuser, nor see or change
    a superuser's account (e.g. set its password): a superuser has every permission, so it would get around
    the roles."""

    def get_queryset(self, request):
        users = super().get_queryset(request)
        return users if request.user.is_superuser else users.filter(is_superuser=False)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else (*fields, "is_superuser")
