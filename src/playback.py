"""Playback of a video file (pause, seek, position) and the pacing of frames read from a source."""
import threading
import time
from typing import Optional

import cv2

# Live-like file playback: skip up to this many frames with grab(); further behind, seek instead
_MAX_GRAB_SKIP = 15
_DROP_LOG_EVERY = 300
# Paced live streams (HLS): a frame this far behind its slot restarts the pacing clock (stall / reconnect)
_PACE_RESYNC_S = 2.0


class Playback:
    """Pause, seek and position of a video file. A live stream keeps the defaults: never paused, position 0.

    Seeks are only requested here; the capture thread applies them (take_seek), also while paused.
    holding is True while playback waits for the processing thread (start of the video, after a seek).
    """

    def __init__(self, paused: bool = False):
        self._lock = threading.Lock()
        self._paused = paused
        self._position_ms = 0.0
        self._total_ms = 0.0
        self._seek_request_ms: Optional[float] = None
        self.holding = False

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def toggle_pause(self, paused: Optional[bool] = None) -> bool:
        """Flip pause, or set it when paused is given. Returns the new state."""
        with self._lock:
            self._paused = (not self._paused) if paused is None else paused
            paused = self._paused
        print(f"[Video] {'PAUSED' if paused else 'RESUMED'}")
        return paused

    def rewind_paused(self) -> None:
        """End of the video: back to the start, paused, like a video player."""
        with self._lock:
            self._paused = True
            self._position_ms = 0.0

    def position(self):
        """(position_ms, total_ms)."""
        with self._lock:
            return self._position_ms, self._total_ms

    def set_position(self, position_ms: float) -> None:
        with self._lock:
            self._position_ms = position_ms

    def set_total(self, total_ms: float) -> None:
        with self._lock:
            self._total_ms = max(0.0, total_ms)

    def seek(self, position_ms: float) -> None:
        """Ask the capture thread to jump to a position (kept 0.5 s before the end)."""
        with self._lock:
            total = self._total_ms
            self._seek_request_ms = max(0.0, min(float(position_ms), total - 500.0 if total > 1000 else total))

    @property
    def seek_pending(self) -> bool:
        return self._seek_request_ms is not None

    def take_seek(self) -> Optional[float]:
        """The requested position, once (None when no seek is pending); it becomes the current position."""
        with self._lock:
            target, self._seek_request_ms = self._seek_request_ms, None
            if target is not None:
                self._position_ms = target
            return target


class RealtimeClock:
    """Plays a file like a live camera: a wall clock decides which frame is "on air" now. When decoding or
    processing falls behind, frames are skipped instead of slowing the video down."""

    def __init__(self, frame_interval: float):
        self.frame_interval = frame_interval
        self._t0 = None        # wall time at the clock anchor; None = anchor on the next frame
        self._ms0 = 0.0        # video position (ms) at the anchor
        self._dropped = 0

    def restart(self) -> None:
        """Anchor again on the next frame (after a seek, pause or wait), so that time is not "caught up"."""
        self._t0 = None

    def catch_up(self, cap) -> None:
        """Skip the frames whose time has already passed before the next one is read."""
        pos_now = cap.get(cv2.CAP_PROP_POS_MSEC)
        if self._t0 is None:
            self._t0, self._ms0 = time.time(), pos_now
        elapsed_ms = (time.time() - self._t0) * 1000.0
        lag_frames = int((elapsed_ms - (pos_now - self._ms0)) / 1000.0 / self.frame_interval)
        if lag_frames > _MAX_GRAB_SKIP:
            # Far behind: seeking is cheaper than decoding every skipped frame
            cap.set(cv2.CAP_PROP_POS_MSEC, self._ms0 + elapsed_ms)
            self._dropped += lag_frames
        elif lag_frames > 0:
            for _ in range(lag_frames):
                if not cap.grab():
                    break
                self._dropped += 1
        if self._dropped >= _DROP_LOG_EVERY:
            print(f"[Video] Behind real time: dropped {self._dropped} frames")
            self._dropped = 0

    def wait_for(self, pos_ms: float) -> None:
        """Hold the frame at pos_ms until its time on the wall clock (never sleeps to slow down when behind)."""
        if self._t0 is None:
            return
        sleep_time = self._t0 + (pos_ms - self._ms0) / 1000.0 - time.time()
        if sleep_time > 0:
            time.sleep(min(sleep_time, 1.0))


class LivePacer:
    """HLS over http(s) delivers whole segments in bursts; frames are handed on at the source fps instead."""

    def __init__(self, frame_interval: float):
        self.frame_interval = frame_interval
        self._slot_t = None   # wall time slot of the last frame

    def wait(self) -> None:
        now = time.time()
        if self._slot_t is None or now - self._slot_t > _PACE_RESYNC_S:
            self._slot_t = now
        else:
            self._slot_t += self.frame_interval
        if self._slot_t > now:
            time.sleep(self._slot_t - now)
