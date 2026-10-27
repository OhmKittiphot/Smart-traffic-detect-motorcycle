"""The live view's video over WebRTC: the annotated picture (live_frames.py) as a video track that aiortc
encodes and sends to the browser, instead of one JPEG after another over /stream/. It needs less bandwidth
and has less delay; /stream/ stays as the fallback where WebRTC cannot connect (live-video.js).

The browser sends an offer (views/webrtc.py) and the server answers it. aiortc runs on asyncio, so one event
loop runs in a background thread and the views hand their work to it. A connection ends when the browser
closes it or the viewer's login is no longer valid (security.session_allows)."""
import asyncio
import fractions
import threading
import time

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame
from django.conf import settings
from django.db import close_old_connections

from .engine import current_service
from .live_frames import placeholder_frame
from .security import session_allows

_CLOCK_RATE = 90000   # RTP clock of video
_TIME_BASE = fractions.Fraction(1, _CLOCK_RATE)
# How often an open connection checks that its viewer is still logged in
_SESSION_CHECK_S = 2.0
# How long a view waits for the answer
_ANSWER_TIMEOUT_S = 15
# Open connections one login may have. A page that reconnects (reload, network change) leaves its old one
# open for a while; past this, the oldest are closed, so a login cannot keep opening connections
_MAX_PEERS_PER_SESSION = 2

_loop = None
_loop_lock = threading.Lock()
# Open connection -> its viewer's session key; also keeps them from being garbage collected. Only used on
# the event loop's thread
_peers: dict = {}


def _event_loop() -> asyncio.AbstractEventLoop:
    """The background event loop aiortc runs on, started on first use."""
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True, name="WebRTC").start()
        return _loop


def _login_ended(session_key) -> bool:
    """Run in a worker thread: Django's database calls are not allowed on the event loop."""
    try:
        return not session_allows(session_key, "dashboard.use_dashboard")
    finally:
        close_old_connections()


class LiveTrack(VideoStreamTrack):
    """The live view's picture, STREAM_FPS frames a second, for as long as the viewer's login is valid."""

    def __init__(self, session_key, on_end):
        super().__init__()
        self._session_key = session_key
        self._on_end = on_end
        self._start = None
        self._count = 0
        self._next_check = 0.0
        self._card = None   # shown while detection is off

    async def _wait_for_next_frame(self) -> int:
        """Sleep until the next frame is due; returns its timestamp on the RTP clock."""
        if self._start is None:
            self._start = time.time()
        else:
            self._count += 1
            await asyncio.sleep(max(0.0, self._start + self._count / settings.STREAM_FPS - time.time()))
        return int(self._count * _CLOCK_RATE / settings.STREAM_FPS)

    def _picture(self):
        service = current_service()
        picture = service.frames.frame() if service else None
        if picture is None:
            if self._card is None:
                self._card = placeholder_frame("")
            picture = self._card
        return picture

    async def recv(self) -> VideoFrame:
        pts = await self._wait_for_next_frame()
        if time.time() >= self._next_check:
            self._next_check = time.time() + _SESSION_CHECK_S
            if await asyncio.to_thread(_login_ended, self._session_key):
                await self._on_end()
                raise MediaStreamError
        # Drawing the frame takes a while; it must not hold up the event loop
        frame = VideoFrame.from_ndarray(await asyncio.to_thread(self._picture), format="bgr24")
        frame.pts, frame.time_base = pts, _TIME_BASE
        return frame


async def _close(pc: RTCPeerConnection) -> None:
    _peers.pop(pc, None)
    await pc.close()


async def _answer(offer_sdp: str, session_key: str) -> dict:
    own = [pc for pc, key in _peers.items() if key == session_key]   # oldest first
    for old in own[:max(0, len(own) - _MAX_PEERS_PER_SESSION + 1)]:
        await _close(old)

    # No STUN server: the viewers are on the same network as the server, and asking a public STUN server
    # (aiortc's default) for an outside address adds seconds to every connection
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    _peers[pc] = session_key

    @pc.on("connectionstatechange")
    async def on_state():
        if pc.connectionState in ("failed", "closed"):
            await _close(pc)

    try:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
        pc.addTrack(LiveTrack(session_key, lambda: _close(pc)))
        # Returns once the server's network addresses are gathered: they all go in the answer
        await pc.setLocalDescription(await pc.createAnswer())
    except BaseException:   # a bad offer, or cancelled because the view stopped waiting
        await _close(pc)
        raise
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


def answer(offer_sdp: str, session_key: str) -> dict:
    """The answer ({"sdp", "type"}) to a browser's offer of a connection that receives the live video.
    Raises TimeoutError when it takes longer than _ANSWER_TIMEOUT_S (the half-made connection is closed)."""
    future = asyncio.run_coroutine_threadsafe(_answer(offer_sdp, session_key), _event_loop())
    try:
        return future.result(_ANSWER_TIMEOUT_S)
    except TimeoutError:
        future.cancel()
        raise
