import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import cv2

from .frame_exchange import FrameExchange
from .hls import STALE_AFTER_S, format_age, is_hls_url, playlist_age_s
from .playback import LivePacer, Playback, RealtimeClock
from .utils import log, log_error, open_capture


# Backoff between RTSP reconnect attempts: doubles on each failure, resets on the first good frame
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 15.0
# Consecutive grab failures on a file before it is treated as EOF (a corrupt frame also fails grab())
_EOF_FAIL_STREAK = 30
# Longest wait for the processing thread before playback continues anyway
_HOLD_TIMEOUT_S = 20.0
# How often a frozen HLS playlist is checked again while the camera is treated as offline
_STALE_RECHECK_S = 15.0


@dataclass
class _CaptureLoop:
    """What the capture loop keeps from one frame to the next."""
    cap: cv2.VideoCapture
    clock: Optional[RealtimeClock]   # video files: plays them in real time
    pacer: Optional[LivePacer]       # HLS: spreads the frames that arrive in bursts
    reconnect_delay: float = _RECONNECT_MIN_S
    grab_fail_streak: int = 0        # frames of a file that could not be read in a row
    frames_since_start: int = 0      # frames read since the file was opened or rewound
    show_one_frame: bool = False     # read one frame even though playback is paused


class VideoStream:
    """Reads a video file or a live camera (RTSP / HLS) in a background thread.

    frames hands each frame to processing and display (FrameExchange); playback controls a video file
    (Playback). This class owns the capture thread: opening, reconnecting, pacing and end of file.
    """

    def __init__(self, video_camera: str, display_fps: float = 30.0, on_end=None, initial_generation: int = 0,
                 on_seek=None, start_paused: bool = False):
        self.video_camera = video_camera
        self.display_fps = display_fps
        self.on_end = on_end     # called with the new generation when a file ended (back to the start, paused)
        self.on_seek = on_seek   # called with the new generation after a seek
        is_network = video_camera.lower().startswith(("rtsp://", "rtsps://", "http://", "https://"))
        self.is_video_file = not is_network
        # HLS over http(s) delivers whole segments in bursts; frames are paced at the source fps instead
        self._paced_live = is_network and video_camera.lower().startswith(("http://", "https://"))
        self._is_hls = is_hls_url(video_camera)
        # Why a live source shows no video (e.g. its playlist stopped updating), or None
        self.offline_reason = None

        # Start above the previous stream's generation when switching sources, so late results
        # (OCR, YOLO) from the old source are discarded as stale
        self.frames = FrameExchange(initial_generation)
        # start_paused: a file waits on its first frame until play is pressed (a live stream never pauses)
        self.playback = Playback(paused=start_paused and self.is_video_file)
        self.source_fps = float(display_fps)

        self._running = False
        self._thread = None
        self.error = None   # fatal error that stopped capture (e.g. unreadable video file), or None
        # Files: playback holds on the first frame (and after every seek) until the processing thread has
        # finished it, so the video never runs ahead of detection while models warm up
        self._hold_gen = initial_generation if self.is_video_file else None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._capture_fn, daemon=True, name="Capture")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def wait_for_first_frame(self, timeout: float = None) -> bool:
        start = time.time()
        while self._running:
            if self.frames.latest() is not None:
                return True
            if timeout and (time.time() - start) > timeout:
                return False
            time.sleep(0.05)
        return False

    # ----- opening the source -----

    def _fail(self, msg: str):
        log.error("[Video] %s", msg)
        self.error = msg
        self._running = False

    def _read_fps(self, cap) -> float:
        source_fps = cap.get(cv2.CAP_PROP_FPS)
        if not source_fps or source_fps <= 0:
            source_fps = self.display_fps
        self.source_fps = source_fps
        return source_fps

    def _sleep_while_running(self, seconds: float) -> None:
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(0.2)

    def _wait_for_fresh_playlist(self) -> None:
        """Block while an HLS playlist has stopped updating. Opening it anyway would replay its last few
        segments (old video, re-detected and re-reported) and then stall, over and over."""
        if not self._is_hls:
            return
        logged = False
        while self._running:
            age = playlist_age_s(self.video_camera)
            if age is None or age < STALE_AFTER_S:
                if logged:
                    log.info("[Capture] Live stream is updating again.")
                self.offline_reason = None
                return
            self.offline_reason = f"Camera offline: no new video for {format_age(age)}"
            if not logged:
                log.warning("[Capture] Live stream not updating (last update %s ago); treating camera as offline, "
                            "checking every %.0fs.", format_age(age), _STALE_RECHECK_S)
                logged = True
            self._sleep_while_running(_STALE_RECHECK_S)

    def _open(self):
        self._wait_for_fresh_playlist()
        return open_capture(self.video_camera)

    def _reopen(self, cap, delay: float):
        """Release the capture, wait `delay` seconds, and open the source again."""
        try:
            cap.release()
        except Exception as e:
            log_error("Capture", e)
        self._sleep_while_running(delay)
        return self._open()

    # ----- file playback -----

    def _apply_seek(self, cap) -> bool:
        """Perform a pending seek. Starts a new media generation, so tracks from before the jump are
        dropped, and holds playback until the first frame after it has been processed."""
        target = self.playback.take_seek()
        if target is None:
            return False
        generation = self.frames.new_generation()
        cap.set(cv2.CAP_PROP_POS_MSEC, target)
        self._hold_gen = generation
        self._notify(self.on_seek, generation)
        return True

    def _rewind_at_end(self, cap) -> None:
        """Like a video player: back to the first frame, paused, instead of looping."""
        log.info("[Video] End of video. Back to the start, paused.")
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self.playback.rewind_paused()
        self._notify(self.on_end, self.frames.new_generation())

    @staticmethod
    def _notify(callback, generation: int) -> None:
        if callback:
            try:
                callback(generation)
            except Exception as e:
                log_error("Capture", e)

    def _hold_for_processing(self, generation: int) -> None:
        """Block (without advancing the video) until frame generation `generation` was processed."""
        self.playback.holding = True
        deadline = time.time() + _HOLD_TIMEOUT_S
        try:
            while (self._running and self.frames.processed_generation < generation
                   and not self.playback.seek_pending and time.time() < deadline):
                time.sleep(0.02)
        finally:
            self.playback.holding = False

    @staticmethod
    def _at_real_end(cap) -> bool:
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        return total_frames > 0 and cap.get(cv2.CAP_PROP_POS_FRAMES) >= total_frames - 1

    # ----- capture thread -----

    def _capture_fn(self):
        cap = None
        try:
            cap = self._open()
            if self.is_video_file and not cap.isOpened():
                # Unlike a camera that may come back online, a file that can't be opened won't fix itself
                self._fail(f"Cannot open video file: {self.video_camera}")
                return
            self._capture_loop(cap)
        except Exception as e:
            log_error("Capture", e)
            self._fail(f"Capture thread stopped: {type(e).__name__}: {e}")
        finally:
            self._running = False
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    def _make_pacing(self, cap):
        """(clock, pacer): a real-time clock for files, a pacer for HLS, neither for RTSP."""
        if self.is_video_file:
            source_fps = self._read_fps(cap)
            total_ms = cap.get(cv2.CAP_PROP_FRAME_COUNT) / source_fps * 1000.0
            self.playback.set_total(total_ms)
            duration_str = str(timedelta(milliseconds=int(max(0.0, total_ms)))).split('.')[0]
            log.info("[Video] Playback FPS: %.1f (source)  |  Duration: %s", source_fps, duration_str)
            return RealtimeClock(1.0 / source_fps), None
        if self._paced_live:
            return None, LivePacer(1.0 / self._read_fps(cap))
        return None, None

    def _capture_loop(self, cap):
        loop = _CaptureLoop(cap, *self._make_pacing(cap))
        # A seek while paused still shows (and detects) the new position; a file opened paused, its first frame
        loop.show_one_frame = self.playback.is_paused()

        while self._running:
            clock = loop.clock
            if clock and self._apply_seek(loop.cap):
                clock.restart()
                loop.grab_fail_streak = 0
                loop.show_one_frame = self.playback.is_paused()

            if clock and self.playback.is_paused() and not loop.show_one_frame:
                clock.restart()   # re-anchor on resume so paused time is not "caught up"
                time.sleep(0.05)
                continue
            loop.show_one_frame = False

            try:
                if not loop.cap.isOpened():
                    self._reconnect(loop, "[Capture] Source closed. Reopening in %.0fs...")
                    continue
                if clock:
                    clock.catch_up(loop.cap)
                if not loop.cap.grab():
                    if not self._after_failed_grab(loop):
                        return
                    continue
                loop.grab_fail_streak = 0
                ret, frame = loop.cap.retrieve()
                if not ret or frame is None:
                    time.sleep(0.02)
                    continue
                pos_ms = self._publish(loop, frame)
            except Exception as e:
                log_error("Capture", e)
                time.sleep(0.5)
                continue

            if clock:
                clock.wait_for(pos_ms)
            elif loop.pacer:
                loop.pacer.wait()

    def _reconnect(self, loop, message: str) -> None:
        """Open the source again after a wait that doubles on each failure (reset by the next good frame)."""
        log.warning(message, loop.reconnect_delay)
        loop.cap = self._reopen(loop.cap, loop.reconnect_delay)
        loop.reconnect_delay = min(loop.reconnect_delay * 2, _RECONNECT_MAX_S)
        if loop.clock:
            if loop.cap.isOpened():
                loop.clock.frame_interval = 1.0 / self._read_fps(loop.cap)
            loop.clock.restart()

    def _after_failed_grab(self, loop) -> bool:
        """A frame could not be read: reconnect a live stream, rewind a file that ended. False when capture
        must stop (a file with no frame at all)."""
        if not loop.clock:
            self._reconnect(loop, "[RTSP] Stream read failed. Reconnecting in %.0fs...")
            return True
        loop.grab_fail_streak += 1
        # A corrupt / undecodable frame mid-file also fails grab(), so only treat it as the
        # end near the last frame or after repeated failures
        if not (self._at_real_end(loop.cap) or loop.grab_fail_streak >= _EOF_FAIL_STREAK):
            time.sleep(0.05)
            return True
        if loop.frames_since_start == 0:
            # A whole pass produced no frame: the file has nothing to show
            self._fail(f"No decodable frames in video file: {self.video_camera}")
            return False
        self._rewind_at_end(loop.cap)
        loop.grab_fail_streak = loop.frames_since_start = 0
        loop.clock.restart()
        loop.show_one_frame = True   # show (and detect) the first frame while paused
        return True

    def _publish(self, loop, frame) -> float:
        """Hand a frame to processing and display. Returns its position in a video file (ms; 0 for live)."""
        if loop.reconnect_delay > _RECONNECT_MIN_S:
            log.info("[Capture] Source recovered.")
        loop.reconnect_delay = _RECONNECT_MIN_S
        loop.frames_since_start += 1

        pos_ms = loop.cap.get(cv2.CAP_PROP_POS_MSEC) if loop.clock else 0.0
        if loop.clock:
            self.playback.set_position(pos_ms)
        generation = self.frames.publish(frame, pos_ms / 1000.0 if loop.clock else time.time())

        if self._hold_gen is not None and generation >= self._hold_gen:
            self._hold_gen = None
            self._hold_for_processing(generation)
            if loop.clock:
                loop.clock.restart()   # start the playback clock from here, not from before the wait
        return pos_ms
