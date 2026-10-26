import threading
import time
from collections import deque


class ProcessingStats:
    """How fast the processing thread keeps up: frames processed per second, and how far behind the frame
    on screen it is."""

    def __init__(self, samples: int = 30):
        self._lock = threading.Lock()
        self._done_t = deque(maxlen=samples)   # perf_counter() of each processed frame
        # (media generation, source time) of the last processed frame; source time is the video position
        # for files and the wall clock for live
        self._last = None

    def frame_done(self, generation: int, frame_t: float) -> None:
        with self._lock:
            self._done_t.append(time.perf_counter())
            self._last = (generation, frame_t)

    def forget_last(self) -> None:
        """Nothing processed yet in the new media session."""
        with self._lock:
            self._last = None

    def clear(self) -> None:
        with self._lock:
            self._done_t.clear()
            self._last = None

    def fps(self) -> float:
        """Frames processed per second, over the last few."""
        with self._lock:
            samples = list(self._done_t)
        span = samples[-1] - samples[0] if len(samples) > 1 else 0.0
        return (len(samples) - 1) / span if span > 0 else 0.0

    def lag_s(self, stream) -> float:
        """How far processing is behind the frame on screen, in seconds (0 when paused or idle)."""
        with self._lock:
            last_gen, last_t = self._last or (None, None)
        if last_gen != stream.frames.generation:
            return 0.0   # nothing processed yet since the last seek / end of video / source switch
        if stream.is_video_file:
            if stream.playback.is_paused():
                return 0.0
            position_ms, _ = stream.playback.position()
            return max(0.0, position_ms / 1000.0 - last_t)
        if stream.frames.age() > 2.0:
            return 0.0
        return max(0.0, time.time() - last_t)
