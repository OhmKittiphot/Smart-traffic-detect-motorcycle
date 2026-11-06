# Helpers shared by the views.
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from ..engine import current_service
from ..models import AuditLog
from ..security import client_ip


def audit(request, action, detail=""):
    # Record who did what, and from where, in the audit log (shown in /admin/).
    AuditLog.objects.create(user=request.user, action=action, detail=detail[:255], ip_address=client_ip(request))


def conflict(error):
    # 409: the request is valid, but the video engine cannot do it right now (not ready, wrong mode...).
    return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)


def bad_request(error):
    return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class DetectionOff(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = _("Detection is not running")


def running_service():
    # The detection service for a live view request; 409 while detection is off. Requests never start
    # it: only staff logging in does (engine.py).
    service = current_service()
    if service is None:
        raise DetectionOff()
    return service


def jpeg_response(jpeg: bytes):
    # A frame for the calibration editor; never cached, it changes with every request.
    response = HttpResponse(jpeg, content_type="image/jpeg")
    response["Cache-Control"] = "no-store"
    return response
