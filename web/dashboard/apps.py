from django.apps import AppConfig
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save


def _start_detection(sender, request, user, **kwargs):
    from .engine import staff_logged_in

    if user.has_perm("dashboard.use_dashboard"):
        staff_logged_in(request.session.session_key)


def _stop_detection(sender, request=None, **kwargs):
    from .engine import staff_logged_out

    # Sent before the session is flushed, so its key still names this browser's login
    staff_logged_out(request.session.session_key if request is not None else None)


class DashboardConfig(AppConfig):
    # Detection runs while staff are logged in: a live camera all the time, a video file while it plays
    # (see engine.py). It stops as soon as the last of them logs out
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"
    verbose_name = "CCTV Dashboard"

    def ready(self):
        user_logged_in.connect(_start_detection, dispatch_uid="dashboard_start_detection")
        user_logged_out.connect(_stop_detection, dispatch_uid="dashboard_stop_detection")

        from .models import VehicleRecord
        from .record_feed import record_saved

        # Wakes the event streams that push new records to the pages
        post_save.connect(record_saved, sender=VehicleRecord, dispatch_uid="dashboard_record_feed")
