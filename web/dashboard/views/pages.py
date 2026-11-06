# The HTML pages. Their data comes from the API views; these only render the page around it.
from pathlib import Path

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from ..models import STREAM_FPS_CHOICES, CameraSettings, SystemSettings, UserPreferences, VehicleRecord
from ..serializers import PreferencesSerializer
from ..sources import list_live_sources, list_video_files


class LoginView(auth_views.LoginView):
    template_name = "dashboard/login.html"
    redirect_authenticated_user = True


def lockout(request, original_response=None, credentials=None):
    # django-axes: too many failed logins for this username + IP.
    return render(request, "dashboard/login.html", {
        "locked_out": True,
        "cooloff_minutes": int(settings.AXES_COOLOFF_TIME.total_seconds() // 60),
    }, status=429)


@require_GET
def dashboard(request):
    # The live view.
    prefs = UserPreferences.load(request.user)
    return render(request, "dashboard/dashboard.html", {
        "video_files": list_video_files(),
        "live_sources": [(key, label) for key, (label, _url) in list_live_sources().items()],
        "saved": CameraSettings.load(),
        "rules": SystemSettings.load(),
        "prefs": prefs,
        "prefs_data": PreferencesSerializer(prefs).data,
        "stream_fps_choices": STREAM_FPS_CHOICES,
    })


def _source_options():
    # Source dropdown options: {"live": [...], "file": [...], "location": [...]} of (value, label).
    # value is "<source_mode>:<source_name>" as records store it, or "location:<name>".
    names = {"live": {"", *(label for label, _url in list_live_sources().values())}, "file": set(list_video_files())}
    for mode, name in VehicleRecord.objects.order_by().values_list("source_mode", "source_name").distinct():
        names.setdefault(mode, set()).add(name)
    names["location"] = set(VehicleRecord.objects.exclude(location="").order_by().values_list("location", flat=True)
                         .distinct())
    label = lambda mode, name: name or (_("IP camera (RTSP)") if mode == "live" else _("Video file"))
    return {mode: [(f"{mode}:{name}", label(mode, name)) for name in sorted(found, key=lambda n: (n != "", n.lower()))]
            for mode, found in names.items()}


def _province_names():
    # (Thai name, English name) of every province for the dropdown: Bangkok first, then the others in
    # Thai alphabetical order, as PROVINCE_NAMES_EN lists them (Python cannot sort Thai text correctly).
    from src.province import PROVINCE_NAMES_EN

    bangkok = "กรุงเทพมหานคร"
    return [(bangkok, PROVINCE_NAMES_EN[bangkok])] + [item for item in PROVINCE_NAMES_EN.items() if item[0] != bangkok]


@require_GET
def plate_search(request):
    # Plate search on its own.
    return render(request, "dashboard/search.html", {"provinces": _province_names(), "sources": _source_options()})


@require_GET
def violations(request):
    # Violation statistics, with the plate search under them.
    return render(request, "dashboard/violations.html",
                  {"provinces": _province_names(), "sources": _source_options()})


def _detection_models(config):
    # The models detection loads (AppConfig paths) and what each one is for, for the system status page.
    def found(path):   # AppConfig paths are relative to the project root
        return (Path(settings.PROJECT_DIR) / path).exists()

    rider_found = found(config.rider_model_path)
    models = [
        (_("Motorcycle detector"), config.motorcycle_model_path, _("finds motorcycles in the frame")),
        (_("Plate detector"), config.plate_model_path, _("finds the licence plate on each motorcycle")),
        (_("Plate OCR"), config.plate_ocr_model_path, _("reads the plate's letters and number")),
        (_("Rider classifier"), config.rider_model_path, _("counts riders and checks their helmets")),
        (_("Helmet detector"), config.helmet_model_path,
         _("not used while the rider classifier is there") if rider_found else _("helmet / no helmet on each head")),
    ]
    return [{"name": name, "file": Path(path).name, "purpose": purpose, "found": found(path)}
            for name, path, purpose in models]


@require_GET
def system_status(request):
    # Admin: camera and system health (it only checks detection, it never starts it).
    from src.app_config import AppConfig

    models = _detection_models(AppConfig.from_env())
    return render(request, "dashboard/system.html",
                  {"models": models, "models_missing": sum(not m["found"] for m in models)})
