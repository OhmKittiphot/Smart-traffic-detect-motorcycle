"""Helpers shared by the views."""
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response

from ..models import AuditLog


def audit(request, action, detail=""):
    """Record who did what, and from where, in the audit log (shown in /admin/)."""
    AuditLog.objects.create(user=request.user, action=action, detail=detail[:255],
                            ip_address=request.META.get("REMOTE_ADDR"))


def conflict(error):
    """409: the request is valid, but the video engine cannot do it right now (not ready, wrong mode...)."""
    return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)


def bad_request(error):
    return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


def jpeg_response(jpeg: bytes):
    """A frame for the calibration editor; never cached, it changes with every request."""
    response = HttpResponse(jpeg, content_type="image/jpeg")
    response["Cache-Control"] = "no-store"
    return response
