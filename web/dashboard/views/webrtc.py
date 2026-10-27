"""The live view's video over WebRTC (dashboard/webrtc.py): the browser's offer in, the server's answer out."""
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import webrtc
from ..serializers import WebRTCOfferSerializer


class WebRTCOfferView(APIView):
    def post(self, request):
        """{"sdp": "<the browser's offer>"} -> {"sdp", "type": "answer"}: a connection that receives the
        live video, for as long as this session lasts."""
        offer = WebRTCOfferSerializer(data=request.data)
        offer.is_valid(raise_exception=True)
        return Response(webrtc.answer(offer.validated_data["sdp"], request.session))
