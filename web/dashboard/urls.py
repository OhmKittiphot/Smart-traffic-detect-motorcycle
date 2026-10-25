from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.AdminLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("stream/", views.stream, name="stream"),
    path("snapshot/<int:pk>/", views.snapshot, name="snapshot"),
    path("api/status/", views.StatusView.as_view(), name="api_status"),
    path("api/vehicles/", views.VehicleListView.as_view(), name="api_vehicles"),
    path("api/vehicles/clear/", views.VehicleClearView.as_view(), name="api_vehicles_clear"),
    path("api/source/", views.SourceView.as_view(), name="api_source"),
    path("api/pause/", views.PauseView.as_view(), name="api_pause"),
    path("api/seek/", views.SeekView.as_view(), name="api_seek"),
    path("api/calibration/", views.CalibrationView.as_view(), name="api_calibration"),
    path("api/calibration/frame/", views.CalibrationFrameView.as_view(), name="api_calibration_frame"),
    path("api/calibration/save/", views.CalibrationSaveView.as_view(), name="api_calibration_save"),
    path("api/calibration/remove/", views.CalibrationRemoveView.as_view(), name="api_calibration_remove"),
]
