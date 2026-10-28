import time
from collections import deque
from typing import Dict, Optional

import numpy as np

from .rider_classifier import EMPTY, HELMET, NO_HELMET, summarize


class RiderVoting:
    """Averages per-frame rider/helmet probabilities over each motorcycle track to stabilize the result.
    Each frame's vote is weighted (by its crop size), so a close, clear view outweighs a distant one."""

    def __init__(self, window: int = 15, min_frames: int = 3, max_age_s: float = 6.0, seat_threshold: float = 0.5):
        self.window = window
        self.min_frames = min_frames
        self.max_age_s = max_age_s
        self.seat_threshold = seat_threshold
        self._probs: Dict[int, deque] = {}
        self._weights: Dict[int, deque] = {}
        self._last_seen: Dict[int, float] = {}

    def reset(self) -> None:
        """Forget all tracks (track IDs restart after a video loop, seek or source switch)."""
        self._probs.clear()
        self._weights.clear()
        self._last_seen.clear()

    def update(self, track_id: int, probs: np.ndarray, weight: float = 1.0) -> None:
        """probs: (positions, states) probabilities for one crop of this track."""
        if track_id not in self._probs:
            self._probs[track_id] = deque(maxlen=self.window)
            self._weights[track_id] = deque(maxlen=self.window)
        self._probs[track_id].append(probs)
        self._weights[track_id].append(weight)
        self._last_seen[track_id] = time.time()

    def _states(self, mean: np.ndarray) -> list:
        """Each position's state: taken once P(not empty) reaches seat_threshold, then helmet or not by
        whichever of the two is more likely."""
        return [EMPTY if 1.0 - p[EMPTY] < self.seat_threshold
                else NO_HELMET if p[NO_HELMET] > p[HELMET] else HELMET
                for p in mean]

    def get(self, track_id: int) -> Optional[Dict]:
        """Smoothed summary for a track, or None if it was not seen in enough frames yet."""
        hist = self._probs.get(track_id)
        if not hist or len(hist) < self.min_frames:
            return None
        mean = np.average(np.stack(hist), axis=0, weights=self._weights[track_id])
        result = summarize(self._states(mean))
        result["frames"] = len(hist)
        return result

    def prune(self) -> None:
        now = time.time()
        for tid in [t for t, ts in self._last_seen.items() if now - ts > self.max_age_s]:
            self._probs.pop(tid, None)
            self._weights.pop(tid, None)
            self._last_seen.pop(tid, None)
