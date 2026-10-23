import threading
import time
from datetime import timedelta
import cv2

from utils import log_error, open_capture


# Backoff between RTSP reconnect attempts: doubles on each failure, resets on the first good frame
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 15.0
# Consecutive grab failures on a file before it is treated as EOF (a corrupt frame also fails grab())
_EOF_FAIL_STREAK = 30
# Live-like file playback: skip up to this many frames with grab(); further behind, seek instead
_MAX_GRAB_SKIP = 15
_DROP_LOG_EVERY = 300


class VideoStream:
    """Manages video acquisition from a video file or live RTSP camera feed in a background thread."""

    def __init__(self, video_camera: str, display_fps: float = 30.0, on_loop=None):
        self.video_camera = video_camera
        self.display_fps = display_fps
        self.on_loop = on_loop
        self.is_video_file = not video_camera.lower().startswith("rtsp://")

        self._running = False
        self._thread = None
        self._error = None

        self._paused = False
        self._pause_lock = threading.Lock()

        self._source_fps = float(display_fps)
        self._video_pos_ms = 0.0
        self._video_total_ms = 0.0
        self._media_generation = 0

        self._latest_frame = None
        self._frame_for_proc = None
        self._frame_for_proc_t = 0.0
        self._frame_for_proc_gen = 0

        self.frame_lock = threading.Lock()
        self.proc_lock = threading.Lock()
        self.frame_ready = threading.Event()

    @property
    def source_fps(self) -> float:
        return self._source_fps

    @property
    def media_generation(self) -> int:
        with self.frame_lock:
            return self._media_generation

    @property
    def error(self):
        """Fatal error that stopped capture (e.g. unreadable video file), or None."""
        return self._error

    @property
    def is_running(self) -> bool:
        return self._running

    def is_paused(self) -> bool:
        with self._pause_lock:
            return self._paused

    def toggle_pause(self) -> bool:
        with self._pause_lock:
            self._paused = not self._paused
            paused = self._paused
        print(f"[Video] {'PAUSED' if paused else 'RESUMED'}")
        return paused

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._capture_fn, daemon=True, name="Capture")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def wait_for_first_frame(self, timeout: float = None):
        start = time.time()
        while self._running:
            with self.frame_lock:
                if self._latest_frame is not None:
                    return True
            if timeout and (time.time() - start) > timeout:
                return False
            time.sleep(0.05)
        return False

    def get_display_data(self):
        """Returns (latest_frame, video_pos_ms, video_total_ms)."""
        with self.frame_lock:
            return self._latest_frame, self._video_pos_ms, self._video_total_ms

    def get_proc_data(self):
        """Returns (frame_for_proc, frame_for_proc_t, frame_for_proc_gen)."""
        with self.proc_lock:
            return self._frame_for_proc, self._frame_for_proc_t, self._frame_for_proc_gen

    def wait_frame_ready(self, timeout: float = 2.0) -> bool:
        ready = self.frame_ready.wait(timeout=timeout)
        self.frame_ready.clear()
        return ready

    def _fail(self, msg: str):
        print(f"[Video] {msg}")
        self._error = msg
        self._running = False

    def _read_file_fps(self, cap) -> float:
        source_fps = cap.get(cv2.CAP_PROP_FPS)
        if not source_fps or source_fps <= 0:
            source_fps = self.display_fps
        self._source_fps = source_fps
        return source_fps

    def _reopen(self, cap, delay: float):
        """Release the capture, wait `delay` seconds, and open the source again."""
        try:
            cap.release()
        except Exception as e:
            log_error("Capture", e)
        time.sleep(delay)
        return open_capture(self.video_camera)

    def _capture_fn(self):
        cap = None
        try:
            cap = open_capture(self.video_camera)
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

    def _capture_loop(self, cap):
        if self.is_video_file:
            source_fps = self._read_file_fps(cap)
            frame_interval = 1.0 / source_fps
            total_ms = cap.get(cv2.CAP_PROP_FRAME_COUNT) / source_fps * 1000.0
            with self.frame_lock:
                self._video_total_ms = max(0.0, total_ms)
            duration_str = str(timedelta(milliseconds=int(max(0.0, total_ms)))).split('.')[0]
            print(f"[Video] Playback FPS: {source_fps:.1f} (source)  |  Duration: {duration_str}")
        else:
            frame_interval = 0.0

        grab_fail_streak = 0
        frames_since_loop = 0
        reconnect_delay = _RECONNECT_MIN_S

        # Live-like playback for files: a wall clock decides which frame "is on air" now.
        # When decoding/processing falls behind, frames are skipped instead of slowing the video.
        clock_t0 = None       # wall time at the clock anchor
        clock_ms0 = 0.0       # video position (ms) at the clock anchor
        dropped = 0

        while self._running:
            # --- Pause: stop reading new frames when paused ---
            if self.is_video_file and self.is_paused():
                clock_t0 = None   # re-anchor on resume so paused time is not "caught up"
                time.sleep(0.05)
                continue

            try:
                if not cap.isOpened():
                    print(f"[Capture] Source closed. Reopening in {reconnect_delay:.0f}s...")
                    cap = self._reopen(cap, reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, _RECONNECT_MAX_S)
                    if self.is_video_file and cap.isOpened():
                        frame_interval = 1.0 / self._read_file_fps(cap)
                    clock_t0 = None
                    continue

                if self.is_video_file:
                    pos_now = cap.get(cv2.CAP_PROP_POS_MSEC)
                    if clock_t0 is None:
                        clock_t0, clock_ms0 = time.time(), pos_now
                    lag_frames = int(((time.time() - clock_t0) * 1000.0 - (pos_now - clock_ms0)) / 1000.0 / frame_interval)
                    if lag_frames > _MAX_GRAB_SKIP:
                        # Far behind: seeking is cheaper than decoding every skipped frame
                        cap.set(cv2.CAP_PROP_POS_MSEC, clock_ms0 + (time.time() - clock_t0) * 1000.0)
                        dropped += lag_frames
                    elif lag_frames > 0:
                        for _ in range(lag_frames):
                            if not cap.grab():
                                break
                            dropped += 1
                    if dropped >= _DROP_LOG_EVERY:
                        print(f"[Video] Behind real time: dropped {dropped} frames")
                        dropped = 0

                if not cap.grab():
                    if self.is_video_file:
                        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                        current_frame = cap.get(cv2.CAP_PROP_POS_FRAMES)
                        at_real_end = total_frames > 0 and current_frame >= total_frames - 1
                        grab_fail_streak += 1
                        # Corrupt/undecodable frame mid-stream also fails grab(), so only
                        # treat this as real EOF near the last frame or after repeated failures.
                        if at_real_end or grab_fail_streak >= _EOF_FAIL_STREAK:
                            if frames_since_loop == 0:
                                # A whole pass produced no frame: looping again would spin forever
                                self._fail(f"No decodable frames in video file: {self.video_camera}")
                                return
                            print("[Video] End of video. Looping...")
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            with self.frame_lock:
                                self._video_pos_ms = 0.0
                                self._media_generation += 1
                                cur_gen = self._media_generation
                            if self.on_loop:
                                try:
                                    self.on_loop(cur_gen)
                                except Exception as e:
                                    log_error("Capture", e)
                            grab_fail_streak = 0
                            frames_since_loop = 0
                            clock_t0 = None
                        else:
                            time.sleep(0.05)
                    else:
                        print(f"[RTSP] Stream read failed. Reconnecting in {reconnect_delay:.0f}s...")
                        cap = self._reopen(cap, reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, _RECONNECT_MAX_S)
                    continue

                grab_fail_streak = 0

                ret, frame = cap.retrieve()
                if not ret or frame is None:
                    time.sleep(0.02)
                    continue

                if reconnect_delay > _RECONNECT_MIN_S:
                    print("[Capture] Source recovered.")
                reconnect_delay = _RECONNECT_MIN_S
                frames_since_loop += 1

                pos_ms = 0.0
                if self.is_video_file:
                    pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                    with self.frame_lock:
                        self._video_pos_ms = pos_ms

                with self.frame_lock:
                    self._latest_frame = frame
                    media_gen = self._media_generation

                frame_t = pos_ms / 1000.0 if self.is_video_file else time.time()
                with self.proc_lock:
                    self._frame_for_proc = frame
                    self._frame_for_proc_t = frame_t
                    self._frame_for_proc_gen = media_gen

                self.frame_ready.set()

            except Exception as e:
                log_error("Capture", e)
                time.sleep(0.5)
                continue

            # Hold each frame until its time on the wall clock (never sleep to "slow down" when behind)
            if self.is_video_file and clock_t0 is not None:
                sleep_time = clock_t0 + (pos_ms - clock_ms0) / 1000.0 - time.time()
                if sleep_time > 0:
                    time.sleep(min(sleep_time, 1.0))
