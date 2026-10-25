from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect


class PagePermissionMiddleware:
    # Open without the check: Django admin checks its own permissions
    allowed_paths = ["/login/", "/logout/", "/admin/", "/static/"]
    # Permission needed per path prefix (first match wins); every other page needs use_dashboard
    path_permissions = [
        ("/system/", "dashboard.view_system_status"),
        ("/api/system/calibration/", "dashboard.manage_calibration"),
        ("/api/system/", "dashboard.view_system_status"),
        ("/search/", "dashboard.view_records"),
        ("/violations/", "dashboard.view_records"),
        ("/api/vehicles/search/", "dashboard.view_records"),
        ("/api/vehicles/delete/", "dashboard.delete_records"),
        ("/api/stats/", "dashboard.view_records"),
        ("/snapshot/", "dashboard.view_records"),
    ]
    # Where a user who opens a page they may not use is sent: the first one they have permission for
    home_pages = [
        ("dashboard.use_dashboard", "dashboard"),
        ("dashboard.view_records", "violations"),
        ("dashboard.view_system_status", "system_status"),
    ]
    # Answered with JSON instead of a redirect, since they are called by the page's JavaScript
    api_paths = ["/api/", "/stream/", "/snapshot/"]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if any(path.startswith(p) for p in self.allowed_paths):
            return self.get_response(request)

        is_api = any(path.startswith(p) for p in self.api_paths)
        user = request.user
        if not user.is_authenticated:
            if is_api:
                return JsonResponse({"detail": "authentication required"}, status=401)
            return redirect_to_login(request.get_full_path())   # back to this page after login

        needed = next((perm for prefix, perm in self.path_permissions if path.startswith(prefix)),
                      "dashboard.use_dashboard")
        if user.has_perm(needed):
            return self.get_response(request)

        if is_api:
            return JsonResponse({"detail": "you do not have permission for this"}, status=403)
        for perm, page in self.home_pages:
            if user.has_perm(perm):
                return redirect(page)
        logout(request)
        return HttpResponseForbidden("This account is not allowed to use the dashboard.")
