from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from .utils import bbox_center, bbox_distance, point_in_bbox

Box = Tuple[int, int, int, int]

# Minimum head height relative to the motorcycle box for the head to count as that bike's rider.
# Measured on the test video: real riders 0.18-0.37, heads of riders on bikes behind 0.05-0.07.
MIN_HEAD_TO_BIKE_HEIGHT = 0.12


class RiderCounter:
    """Riders and helmets of each tracked motorcycle, smoothed over its frames.

    Two sources are combined: the rider classifier on the whole motorcycle (update_summary) and the heads the
    helmet model finds above it (update_heads). The rider count comes from the classifier: in dense traffic the
    heads above a bike include those of its neighbours (on IFGE2443.MOV head counts reached 5 riders on bikes
    with 1-2). Helmet status comes from the heads, seen larger than in the whole motorcycle crop, when there
    are any. Without the classifier both come from the heads."""

    def __init__(self, window: int = 10, min_samples: int = 3, rider_min_frames: int = 3):
        # (helmets, bare heads) per frame for each track; the median over a window smooths flicker
        self.window = window
        self.min_samples = min_samples
        self._helmets: Dict[int, Deque[Tuple[int, int]]] = {}
        # Riders per frame for each track. A passenger is often hidden behind the driver, so a median
        # undercounts; instead a count is trusted once it was seen in rider_min_frames frames of the window
        self.rider_min_frames = rider_min_frames
        self._riders: Dict[int, Deque[int]] = {}
        self._rider_max: Dict[int, int] = {}
        # Smoothed rider classifier result per track (rider_classifier.summarize)
        self._summaries: Dict[int, Dict[str, Any]] = {}

    def reset(self) -> None:
        for counts in (self._helmets, self._riders, self._rider_max, self._summaries):
            counts.clear()

    def forget(self, track_id: int) -> None:
        for counts in (self._helmets, self._riders, self._rider_max, self._summaries):
            counts.pop(track_id, None)

    @staticmethod
    def _count_per_track(track_boxes: Dict[Box, int], heads: List[Box]) -> Dict[int, int]:
        """Assign each head to the nearest motorcycle whose rider zone contains its center."""
        counts = {track_id: 0 for track_id in track_boxes.values()}
        for head in heads:
            center = bbox_center(head)
            best_track_id, best_dist = None, None
            for (x1, y1, x2, y2), track_id in track_boxes.items():
                w, h = x2 - x1, y2 - y1
                # Riders sit above the motorcycle box, so the zone extends upward; heads of the same bike
                # sit within its width, a wide margin would grab heads from the bike behind
                if not point_in_bbox(center, (x1 - 0.05 * w, y1 - 1.0 * h, x2 + 0.05 * w, y2)):
                    continue
                # A rider's head is ~0.18-0.37 of the bike's height; a much smaller head inside the zone
                # belongs to a rider on a bike further back
                if (head[3] - head[1]) < MIN_HEAD_TO_BIKE_HEIGHT * h:
                    continue
                dist = bbox_distance((x1, y1, x2, y2), head)
                if best_dist is None or dist < best_dist:
                    best_track_id, best_dist = track_id, dist
            if best_track_id is not None:
                counts[best_track_id] += 1
        return counts

    def update_heads(self, track_boxes: Dict[Box, int], helmet_bboxes: List[Box], head_bboxes: List[Box],
                     rider_bboxes: Optional[List[Box]] = None) -> None:
        """Record this frame's helmet / bare-head counts and rider count for every motorcycle in track_boxes
        (its box -> track ID). rider_bboxes are all heads (helmeted or not) kept at the lower rider_conf."""
        helmets = self._count_per_track(track_boxes, helmet_bboxes)
        heads = self._count_per_track(track_boxes, head_bboxes)
        riders = (self._count_per_track(track_boxes, rider_bboxes) if rider_bboxes is not None
                  else {tid: helmets[tid] + heads[tid] for tid in helmets})

        for track_id in helmets:
            self._helmets.setdefault(track_id, deque(maxlen=self.window)).append(
                (helmets[track_id], heads[track_id]))
            history = self._riders.setdefault(track_id, deque(maxlen=self.window))
            history.append(riders[track_id])
            if len(history) >= self.rider_min_frames:
                # Highest count reached in at least rider_min_frames frames of the window
                seen = sorted(history, reverse=True)[self.rider_min_frames - 1]
                self._rider_max[track_id] = max(self._rider_max.get(track_id, 0), seen)

    def update_summary(self, track_id: int, summary: Optional[Dict[str, Any]]) -> None:
        """Store the rider classifier's smoothed result for a track (None = not enough frames yet)."""
        if summary is not None:
            self._summaries[track_id] = summary

    def helmet_status(self, track_id: int) -> Optional[Tuple[int, int]]:
        """Smoothed (helmets, bare heads) for a track, or None until enough frames were observed. Bare heads
        are the median over the frames where heads were found (a frame where the helmet model missed them
        says nothing), else the rider classifier's count; every other rider counted wears a helmet."""
        found = [bare for helmets, bare in self._helmets.get(track_id, ()) if helmets + bare]
        summary = self._summaries.get(track_id)
        if len(found) >= self.min_samples:
            bare = sorted(found)[len(found) // 2]
        elif summary is not None:
            bare = summary["no_helmet"]
        else:
            return None
        riders = self.rider_count(track_id)
        bare = min(bare, riders)
        return riders - bare, bare

    def rider_count(self, track_id: int) -> int:
        """Riders on this track: the rider classifier's count, else the most heads seen. A moving motorcycle
        always has a driver, so this is at least 1 even when no head was detected."""
        summary = self._summaries.get(track_id)
        return max(1, summary["riders"] if summary is not None else self._rider_max.get(track_id, 0))

    def rider_seen(self, track_id: int) -> bool:
        """A rider's head was found on this bike in enough frames, or the rider classifier gave a result."""
        return self._rider_max.get(track_id, 0) > 0 or track_id in self._summaries
