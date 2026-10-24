import time
from collections import deque
from typing import Dict, Optional

import numpy as np

from rider_classifier import summarize


class RiderVoting:
    """Averages per-frame rider/helmet probabilities over each motorcycle track to stabilize the result."""

    def __init__(self, window: int = 15, min_frames: int = 3, max_age_s: float = 6.0):
        self.window = window
        self.min_frames = min_frames
        self.max_age_s = max_age_s
        self._probs: Dict[int, deque] = {}
        self._last_seen: Dict[int, float] = {}
        self._reported: set = set()

    def reset(self) -> None:
        """Forget all tracks (track IDs restart after a video loop, seek or source switch)."""
        self._probs.clear()
        self._last_seen.clear()
        self._reported.clear()

    def update(self, track_id: int, probs: np.ndarray) -> None:
        """probs: (positions, states) probabilities for one crop of this track."""
        if track_id not in self._probs:
            self._probs[track_id] = deque(maxlen=self.window)
        self._probs[track_id].append(probs)
        self._last_seen[track_id] = time.time()

    def get(self, track_id: int) -> Optional[Dict]:
        """Smoothed summary for a track, or None if it was not seen in enough frames yet."""
        hist = self._probs.get(track_id)
        if not hist or len(hist) < self.min_frames:
            return None
        mean = np.mean(np.stack(hist), axis=0)
        result = summarize(mean.argmax(-1).tolist())
        result["frames"] = len(hist)
        return result

    def should_report(self, track_id: int) -> bool:
        """True only the first time a track is reported (avoids printing the same violation every frame)."""
        if track_id in self._reported:
            return False
        self._reported.add(track_id)
        return True

    def prune(self) -> None:
        now = time.time()
        for tid in [t for t, ts in self._last_seen.items() if now - ts > self.max_age_s]:
            self._probs.pop(tid, None)
            self._last_seen.pop(tid, None)
            self._reported.discard(tid)
