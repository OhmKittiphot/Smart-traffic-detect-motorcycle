from django.apps import AppConfig


class DashboardConfig(AppConfig):
    # Detection is not started at login: it runs only while staff have the live view open (see engine.py)
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"
    verbose_name = "CCTV Dashboard"
