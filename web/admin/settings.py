"""Django settings for the CCTV admin dashboard.

Secrets and deployment options come from the project's .env (see web/README.md); safe defaults are used
when a value is missing: DEBUG off, only localhost allowed, strict cookies.
"""
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent      # web/
PROJECT_DIR = BASE_DIR.parent                          # repository root (models, input/, .env)

load_dotenv(PROJECT_DIR / ".env")

# The detection pipeline lives in src/ and imports its modules by bare name
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else default


def _env_list(name: str, default: str = "") -> list:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


def _load_secret_key() -> str:
    """DJANGO_SECRET_KEY from .env, or a random key generated once and kept in web/.secret_key."""
    key = os.getenv("DJANGO_SECRET_KEY", "").strip()
    if key:
        return key
    key_file = BASE_DIR / ".secret_key"
    if key_file.is_file():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(64)
    key_file.write_text(key, encoding="utf-8")
    return key


SECRET_KEY = _load_secret_key()
DEBUG = _env_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # Before staticfiles: its runserver command (dashboard/management/commands/runserver.py) replaces that one
    "dashboard",
    "django.contrib.staticfiles",
    "axes",
    "rest_framework",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Headers first, so they also apply to the redirects / 401s of the access check below
    "dashboard.security.SecurityHeadersMiddleware",
    "dashboard.middleware.UserLanguageMiddleware",
    "dashboard.middleware.PagePermissionMiddleware",
    # Must be last: blocks requests from locked-out users / IPs
    "axes.middleware.AxesMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

ROOT_URLCONF = "admin.urls"
WSGI_APPLICATION = "admin.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# The database has a folder of its own at the repository root (SQLite cannot create it)
DATA_DIR = PROJECT_DIR / "database"
DATA_DIR.mkdir(exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        # Reports are written from the detection thread while requests read; wait instead of failing
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Pages are in English until an account picks Thai in Settings (dashboard.middleware.UserLanguageMiddleware)
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("th", "ไทย")]
TIME_ZONE = "Asia/Bangkok"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# Serve app static files straight from the apps, so `collectstatic` is optional
WHITENOISE_USE_FINDERS = True

# Evidence snapshots. Deliberately no MEDIA_URL: they are only served through a login-protected view.
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# --- Sessions & cookies ---
# 8-hour session, counted again only when the session's own data changes (logging in, saving Settings), not
# by the pages' requests and streams; closing the browser logs out
SESSION_COOKIE_AGE = int(timedelta(hours=8).total_seconds())
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Strict"

# --- Security headers ---
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Behind a reverse proxy, which sets the X-Forwarded-* headers (dashboard.security.client_ip)
BEHIND_PROXY = _env_bool("DJANGO_BEHIND_PROXY")

# Behind HTTPS (directly or via a reverse proxy) set DJANGO_HTTPS=1
if _env_bool("DJANGO_HTTPS"):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    if BEHIND_PROXY:
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Login brute-force protection (django-axes) ---
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_CALLABLE = "dashboard.views.pages.lockout"
# The browser's address, from X-Forwarded-For only behind a reverse proxy (without django-ipware, axes
# would otherwise always take REMOTE_ADDR: the proxy's address, shared by every browser)
AXES_CLIENT_IP_CALLABLE = "dashboard.security.client_ip"

# --- API (Django REST Framework) ---
REST_FRAMEWORK = {
    # Same login as the dashboard pages; POSTs need the CSRF token
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    # Logged in; which API a user may call is checked with has_perm in dashboard.middleware, like the pages
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # JSON in and out; the API is only called by the dashboard page
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    # Views with a throttle_scope: the password change checks the current password, which django-axes
    # (login form only) does not guard
    "DEFAULT_THROTTLE_RATES": {"password": "10/hour"},
}

# --- Dashboard ---
# Video files the admin can choose from (only files directly inside this folder are listed/accepted)
VIDEO_INPUT_DIR = PROJECT_DIR / os.getenv("DASHBOARD_VIDEO_DIR", "input")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".ts")
# A live stream with no new frame for this long is shown as "No Signal"
NO_SIGNAL_AFTER_S = 5.0
# The loading indicator shows while detection is this far (seconds) behind the frame on screen
LOADING_LAG_S = 1.0
STREAM_FPS = 12
STREAM_MAX_WIDTH = 1280

# --- Storage (dashboard/storage.py): evidence photos and records are kept within these limits ---
# Longest side and JPEG quality of a saved motorcycle photo (plate photos are kept at their own small size)
SNAPSHOT_MAX_SIDE = _env_int("SNAPSHOT_MAX_SIDE", 720)
SNAPSHOT_JPEG_QUALITY = 82
# Records older than this are deleted with their photos, except confirmed violations
RECORD_RETENTION_DAYS = _env_int("RECORD_RETENTION_DAYS", 90)
# Normal motorcycles and rejected events lose their photos after this (the records stay)
PHOTO_RETENTION_DAYS = _env_int("PHOTO_RETENTION_DAYS", 7)
# Most space the photos may take; the oldest photos that are not evidence go first
MEDIA_MAX_MB = _env_int("MEDIA_MAX_MB", 2048)
CLEANUP_EVERY_H = 6

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "[{name}] {message}", "style": "{"},
        "message": {"format": "{message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "pipeline": {"class": "logging.StreamHandler", "formatter": "message", "stream": "ext://sys.stdout"},
    },
    "loggers": {
        "dashboard": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # The detection pipeline (src/), whose messages carry their own tags ("[Video] ...")
        "pipeline": {"handlers": ["pipeline"], "level": "INFO", "propagate": False},
        "axes": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
