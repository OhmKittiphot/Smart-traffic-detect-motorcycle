from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, JsonResponse
from django.urls import reverse

# Restrictive CSP: no inline scripts/styles, nothing loaded from other origins
_CSP = "; ".join([
    "default-src 'self'",
    "img-src 'self' data: blob:",
    "style-src 'self'",
    "script-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


class AdminOnlyMiddleware:
    """Every page requires a logged-in, active staff account, except the login page and Django admin
    (which has its own staff check). Non-staff accounts are logged out."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        login_path = reverse(settings.LOGIN_URL)
        if path == login_path or path.startswith("/admin/") or path.startswith("/" + settings.STATIC_URL):
            return self.get_response(request)

        user = request.user
        if user.is_authenticated and not (user.is_active and user.is_staff):
            logout(request)
            return HttpResponseForbidden("This account is not allowed to use the dashboard.")
        if not user.is_authenticated:
            if path.startswith("/api/") or path.startswith("/stream/"):
                return JsonResponse({"error": "authentication required"}, status=401)
            return redirect_to_login(request.get_full_path())
        return self.get_response(request)


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
