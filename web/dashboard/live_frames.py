"""The pictures the live view shows: the annotated frame (as JPEG for the MJPEG stream, as pixels for
WebRTC), a card while there is no video, and the raw frame to click calibration points on."""
import threading
import time

import cv2
import numpy as np
from django.conf import settings
from django.utils import timezone


def placeholder_frame(title: str, subtitle: str = "", detail: str = "") -> np.ndarray:
    """Dark 'No Signal'-style card with a timestamp, sized like a 720p camera frame."""
    h, w = 720, 1280
    frame = np.full((h, w, 3), (24, 22, 20), dtype=np.uint8)
    # Faint scan lines so it reads as a dead camera feed rather than a blank page
    frame[::4] = (30, 28, 26)
    font = cv2.FONT_HERSHEY_DUPLEX
    (tw, th), _ = cv2.getTextSize(title or " ", font, 2.4, 3)
    if title:
        cv2.putText(frame, title, ((w - tw) // 2, h // 2), font, 2.4, (235, 235, 235), 3, cv2.LINE_AA)
    if subtitle:
        subtitle = subtitle.replace("·", "-")   # cv2 fonts are ASCII-only; source labels use "·"
        (sw, _), _ = cv2.getTextSize(subtitle, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame, subtitle, ((w - sw) // 2, h // 2 + th + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (150, 150, 150), 2, cv2.LINE_AA)
    if detail:
        (dw, _), _ = cv2.getTextSize(detail, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame, detail, ((w - dw) // 2, h // 2 + th + 75), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (90, 170, 235), 2, cv2.LINE_AA)
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(frame, stamp, (24, h - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (150, 150, 150), 2, cv2.LINE_AA)
    return frame


def fit_width(frame: np.ndarray, max_width: int) -> np.ndarray:
    """The frame, scaled down to max_width when it is wider."""
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    return cv2.resize(frame, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)


def encode_jpeg(frame: np.ndarray, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


class LiveFrames:
    """Frames of one DetectionService, shared by every open stream: each picture is drawn (and encoded as
    JPEG) once, and again only when the frame, the detections or the card's text changed."""

    def __init__(self, service):
        self._service = service
        self._lock = threading.Lock()
        self._frame = None   # the picture now on screen, at most STREAM_MAX_WIDTH wide
        self._jpeg = None    # self._frame as JPEG, once a stream asked for it
        self._key = None

    def _refresh(self) -> None:
        """Draw the picture again if what should be on screen changed (called holding _lock)."""
        key, make = self._picture()
        if key != self._key:
            frame = make()
            if frame is not None:
                self._frame, self._jpeg, self._key = fit_width(frame, settings.STREAM_MAX_WIDTH), None, key

    def frame(self):
        """Current annotated frame (BGR pixels) or a placeholder card, for WebRTC; None before the first."""
        with self._lock:
            self._refresh()
            return self._frame

    def jpeg(self) -> bytes:
        """Current annotated frame as JPEG, or a placeholder card, for the MJPEG stream."""
        with self._lock:
            self._refresh()
            if self._jpeg is None and self._frame is not None:
                self._jpeg = encode_jpeg(self._frame)
            return self._jpeg or b""

    def _picture(self) -> tuple:
        """(key, make): what should be on screen now, and how to draw it. Cards carry the second they were
        drawn in their key, so their clock keeps ticking."""
        service = self._service
        second = int(time.time())
        if service.state == service.STARTING:
            # No text: the page shows its loading animation in the middle of the video instead
            return ("starting", second), lambda: placeholder_frame("")
        if service.state == service.ERROR:
            return ("error", second), lambda: placeholder_frame("SYSTEM ERROR", "See server console")
        app = service.app
        if not service.has_signal():
            configured = service.camera_configured()
            sub = service.source.describe() if configured else "Camera not configured (RTSP_URL)"
            # e.g. the stream's playlist stopped updating (see VideoStream.offline_reason)
            detail = (app.video_stream.offline_reason or "") if configured else ""
            return ("nosignal", sub, detail, second), lambda: placeholder_frame("NO SIGNAL", sub, detail)
        raw = app.video_stream.frames.latest()
        # Re-render only when the frame or the detections changed
        return (id(raw), id(app.latest_detections), id(app.tracker.latest_mc_detections)), app.render_frame

    def raw_jpeg(self) -> bytes:
        """The current frame without overlays, at full resolution, for clicking calibration points on."""
        if not self._service.has_signal():
            raise RuntimeError("No video signal to calibrate on")
        jpeg = encode_jpeg(self._service.app.video_stream.frames.latest(), quality=90)
        if not jpeg:
            raise RuntimeError("Could not capture a frame")
        return jpeg

    def size(self):
        """(width, height) of the current frame, or None."""
        service = self._service
        raw = service.app.video_stream.frames.latest() if service.state == service.RUNNING else None
        return (raw.shape[1], raw.shape[0]) if raw is not None else None
