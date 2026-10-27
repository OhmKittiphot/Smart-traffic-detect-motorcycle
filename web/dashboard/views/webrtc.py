"""The live view's video over WebRTC (dashboard/webrtc.py): the browser's offer in, the server's answer out."""
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import webrtc
from ..serializers import WebRTCOfferSerializer


class WebRTCOfferView(APIView):
    def post(self, request):
        """{"sdp": "<the browser's offer>"} -> {"sdp", "type": "answer"}: a connection that receives the
        live video, for as long as this login is valid."""
        offer = WebRTCOfferSerializer(data=request.data)
        offer.is_valid(raise_exception=True)
        try:
            return Response(webrtc.answer(offer.validated_data["sdp"], request.session.session_key))
        except TimeoutError:
            # The page keeps showing the MJPEG stream (live-video.js)
            return Response({"detail": _("The video connection took too long")},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
