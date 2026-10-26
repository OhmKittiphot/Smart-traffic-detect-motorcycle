from django.apps import AppConfig
from django.contrib.auth.signals import user_logged_out
from django.db.models.signals import post_save


def _stop_detection(sender, request=None, **kwargs):
    from .engine import stop_on_logout

    # Sent before the session is flushed, so its key still names this browser's live view
    stop_on_logout(request.session.session_key if request is not None else None)


class DashboardConfig(AppConfig):
    # Detection is not started at login: it runs only while staff have the live view open (see engine.py)
    # and stops as soon as they log out
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"
    verbose_name = "CCTV Dashboard"

    def ready(self):
        user_logged_out.connect(_stop_detection, dispatch_uid="dashboard_stop_detection")

        from .models import VehicleRecord
        from .record_feed import record_saved

        # Wakes the event streams that push new records to the pages
        post_save.connect(record_saved, sender=VehicleRecord, dispatch_uid="dashboard_record_feed")
