from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "CCTV Admin"
admin.site.site_title = "CCTV Admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("dashboard.urls")),
]
