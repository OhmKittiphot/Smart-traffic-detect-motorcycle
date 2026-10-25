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
