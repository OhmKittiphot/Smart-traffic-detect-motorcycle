import threading
import time


class FrameExchange:
    # Passes frames from the capture thread to processing and display.
    # Each seek, video end or source switch starts a new generation, so results from older frames can be dropped.

    def __init__(self, generation: int = 0):
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._generation = generation
        self._processed_generation = generation - 1
        self._latest = None          # newest frame, for the display
        self._latest_wall_t = 0.0    # when it arrived
        self._for_processing = (None, 0.0, generation)   # (frame, source time, generation)

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def new_generation(self) -> int:
        # Start a new media generation (seek, end of video); returns its number.
        with self._lock:
            self._generation += 1
            return self._generation

    def publish(self, frame, frame_t: float) -> int:
        # A new frame from the capture thread. frame_t is its source time: the video position for files,
        # the wall clock for live streams. Returns the frame's generation.
        with self._lock:
            self._latest = frame
            self._latest_wall_t = time.time()
            self._for_processing = (frame, frame_t, self._generation)
            generation = self._generation
        self._ready.set()
        return generation

    def latest(self):
        # The newest frame (None before the first one).
        with self._lock:
            return self._latest

    def age(self) -> float:
        # Seconds since the last frame arrived (inf before the first frame).
        with self._lock:
            t = self._latest_wall_t
        return time.time() - t if t else float("inf")

    def wait_ready(self, timeout: float = 2.0) -> bool:
        # Wait until a frame was published since the last call (processing thread).
        ready = self._ready.wait(timeout=timeout)
        self._ready.clear()
        return ready

    def for_processing(self):
        # (frame, frame_t, generation) of the newest frame.
        with self._lock:
            return self._for_processing

    def mark_processed(self, generation: int) -> None:
        # Called by the processing thread after it finished a frame of this generation.
        with self._lock:
            self._processed_generation = max(self._processed_generation, generation)

    @property
    def processed_generation(self) -> int:
        with self._lock:
            return self._processed_generation
