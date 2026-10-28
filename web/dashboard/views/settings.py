"""The Settings pop-up of the top bar: violation rules and retention (Admin), preferences and password (everyone)."""
from django.contrib.auth import update_session_auth_hash
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .. import storage
from ..engine import current_service
from ..live_frames import chosen_fps
from ..middleware import remember_language
from ..models import SystemSettings, UserPreferences
from ..serializers import PasswordChangeSerializer, PreferencesSerializer, SystemSettingsSerializer
from .common import audit


class SystemSettingsView(APIView):
    def post(self, request):
        """Admin: change the violation rules and the retention limits. New rules apply at once when
        detection is on; new limits apply at the next clean-up."""
        system = SystemSettings.load()
        serializer = SystemSettingsSerializer(system, data=request.data)
        serializer.is_valid(raise_exception=True)
        changed = [f"{name}={value}" for name, value in serializer.validated_data.items()
                   if getattr(system, name) != value]
        system = serializer.save(updated_by=request.user)
        service = current_service()
        if service is not None:
            service.apply_rules(system.max_riders, float(system.speed_limit_kmh))
        if changed:
            audit(request, "system_settings", ", ".join(changed))
        return Response(SystemSettingsSerializer(system).data)


class SettingsCleanupView(APIView):
    def post(self, request):
        """Admin: apply the retention limits now instead of waiting for the next scheduled clean-up."""
        result = storage.clean_up()
        audit(request, "storage_cleanup",
              f"{result['records_deleted']} records, photos of {result['photos_removed']} records")
        return Response(result)


class PreferencesView(APIView):
    def post(self, request):
        """Save this account's preferences; the Settings pop-up (and the live view's fps menu) send only the
        fields they show. A new frame rate applies to this account's open live views too."""
        serializer = PreferencesSerializer(UserPreferences.load(request.user), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        prefs = serializer.save()
        chosen_fps.cache_clear()
        remember_language(request, prefs.language)
        return Response(serializer.data)


class PasswordView(APIView):
    # Limits guessing the current password from a session left open
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def post(self, request):
        """Change the password of the logged-in account; this session stays logged in, others end."""
        serializer = PasswordChangeSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        audit(request, "change_password")
        return Response({"ok": True})
