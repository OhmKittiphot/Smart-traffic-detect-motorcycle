from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.AdminLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("stream/", views.stream, name="stream"),
    path("snapshot/<int:pk>/", views.snapshot, name="snapshot"),
    path("api/status/", views.api_status, name="api_status"),
    path("api/vehicles/", views.api_vehicles, name="api_vehicles"),
    path("api/vehicles/clear/", views.api_vehicles_clear, name="api_vehicles_clear"),
    path("api/source/", views.api_source, name="api_source"),
    path("api/pause/", views.api_pause, name="api_pause"),
    path("api/seek/", views.api_seek, name="api_seek"),
    path("api/calibration/", views.api_calibration, name="api_calibration"),
    path("api/calibration/frame/", views.api_calibration_frame, name="api_calibration_frame"),
    path("api/calibration/save/", views.api_calibration_save, name="api_calibration_save"),
    path("api/calibration/remove/", views.api_calibration_remove, name="api_calibration_remove"),
]
