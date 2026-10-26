from django.contrib.auth.views import LogoutView
from django.urls import path
from django.views.i18n import JavaScriptCatalog

from .views import calibration, live, pages, records, settings, system

urlpatterns = [
    # Pages
    path("", pages.dashboard, name="dashboard"),
    path("search/", pages.plate_search, name="plate_search"),
    path("violations/", pages.violations, name="violations"),
    path("system/", pages.system_status, name="system_status"),
    path("login/", pages.LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    # gettext() and the Thai texts for the dashboard's JavaScript (djangojs.po)
    path("jsi18n/", JavaScriptCatalog.as_view(packages=["dashboard"]), name="javascript-catalog"),

    # Live view
    path("stream/", live.stream, name="stream"),
    path("api/status/", live.StatusView.as_view(), name="api_status"),
    path("api/vehicles/", live.VehicleListView.as_view(), name="api_vehicles"),
    path("api/vehicles/clear/", live.VehicleClearView.as_view(), name="api_vehicles_clear"),
    path("api/source/", live.SourceView.as_view(), name="api_source"),
    path("api/pause/", live.PauseView.as_view(), name="api_pause"),
    path("api/seek/", live.SeekView.as_view(), name="api_seek"),

    # Records
    path("snapshot/<int:pk>/", records.snapshot, name="snapshot"),
    path("snapshot/<int:pk>/plate/", records.snapshot, {"field": "plate_snapshot"}, name="plate_snapshot"),
    path("api/stats/", records.StatsView.as_view(), name="api_stats"),
    path("api/stats/repeat/", records.RepeatOffendersView.as_view(), name="api_stats_repeat"),
    path("api/violations/new/", records.NewViolationsView.as_view(), name="api_violations_new"),
    path("api/vehicles/delete/", records.VehicleDeleteView.as_view(), name="api_vehicles_delete"),
    # Records list and details pop-up, as HTML for htmx
    path("vehicles/rows/", records.vehicle_rows, name="vehicle_rows"),
    path("vehicles/<int:pk>/", records.vehicle_detail, name="vehicle_detail"),
    path("vehicles/review/<int:pk>/", records.review_vehicle, name="vehicle_review"),

    # Speed calibration: current source (live view) and any source (system page)
    path("api/calibration/", calibration.CalibrationView.as_view(), name="api_calibration"),
    path("api/calibration/frame/", calibration.CalibrationFrameView.as_view(), name="api_calibration_frame"),
    path("api/calibration/save/", calibration.CalibrationSaveView.as_view(), name="api_calibration_save"),
    path("api/calibration/remove/", calibration.CalibrationRemoveView.as_view(), name="api_calibration_remove"),
    path("api/system/calibration/", calibration.SourceCalibrationView.as_view(), name="api_system_calibration"),
    path("api/system/calibration/frame/", calibration.SourceCalibrationFrameView.as_view(),
         name="api_system_calibration_frame"),
    path("api/system/calibration/points/", calibration.SourceCalibrationPointsView.as_view(),
         name="api_system_calibration_points"),
    path("api/system/calibration/remove/", calibration.SourceCalibrationRemoveView.as_view(),
         name="api_system_calibration_remove"),

    # Settings pop-up
    path("api/settings/system/", settings.SystemSettingsView.as_view(), name="api_settings_system"),
    path("api/settings/cleanup/", settings.SettingsCleanupView.as_view(), name="api_settings_cleanup"),
    path("api/settings/preferences/", settings.PreferencesView.as_view(), name="api_settings_preferences"),
    path("api/settings/password/", settings.PasswordView.as_view(), name="api_settings_password"),

    # System status
    path("api/system/", system.SystemStatusView.as_view(), name="api_system"),
    path("api/system/cameras/", system.CameraStatusView.as_view(), name="api_system_cameras"),
]
