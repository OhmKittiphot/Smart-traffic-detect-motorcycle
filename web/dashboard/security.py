import ipaddress
from importlib import import_module
from types import SimpleNamespace

from django.conf import settings
from django.contrib import auth

_SessionStore = import_module(settings.SESSION_ENGINE).SessionStore


def session_allows(session_key, perm: str) -> bool:
    """Whether the login of this session is still valid and has perm. Asked by what outlives its request
    (the video and event streams, the detection engine): the session may have been logged out or have
    expired since, its account disabled, its password changed, or the permission taken away."""
    if not session_key:
        return False
    # Read again from the database: the request's copy does not see any of those changes. Expired sessions
    # read as empty, and get_user() checks the account is active and its password unchanged
    user = auth.get_user(SimpleNamespace(session=_SessionStore(session_key)))
    return user.is_authenticated and user.has_perm(perm)


def client_ip(request):
    """The browser's IP address, for the audit log and django-axes (AXES_CLIENT_IP_CALLABLE). Behind a
    reverse proxy (DJANGO_BEHIND_PROXY=1) REMOTE_ADDR is the proxy's own; the proxy adds the browser's address
    last to X-Forwarded-For (anything before it was sent by the browser itself, so it is not trusted)."""
    if settings.BEHIND_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return request.META.get("REMOTE_ADDR")


# Restrictive CSP: no inline scripts/styles, nothing loaded from other origins except OpenStreetMap for the
# map a video file's place is picked on: its map pictures, and finding places by name (Nominatim)
_CSP = "; ".join([
    "default-src 'self'",
    "img-src 'self' data: blob: https://tile.openstreetmap.org",
    "style-src 'self'",
    "script-src 'self'",
    "connect-src 'self' https://nominatim.openstreetmap.org",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Django admin uses inline styles; it keeps Django's default headers only
        if not request.path.startswith("/admin/"):
            response.setdefault("Content-Security-Policy", _CSP)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if request.user.is_authenticated:
            # Pages and data behind the login must not be cached by the browser or proxies
            response.setdefault("Cache-Control", "no-store")
        return response
