from collections import deque
from typing import Deque, Dict, List, Tuple, Any, Optional
from utils import bbox_distance, point_in_bbox, bbox_center


class MotorcycleTracker:
    """Manages tracking of motorcycles across frames using greedy nearest-neighbor matching."""

    def __init__(self, max_age_s: float, distance_threshold: float):
        self.max_age_s = max_age_s
        self.distance_threshold = distance_threshold
        self.next_mc_id = 1

        # Internal state
        self.motorcycle_tracks: Dict[int, Dict[str, Any]] = {}
        self.bbox_to_track_id: Dict[Tuple[int, int, int, int], int] = {}
        self.latest_mc_detections: List[Tuple[Tuple[int, int, int, int], float]] = []

        # (helmets, bare heads) per frame for each track; the median over a window smooths flicker
        self.helmet_window = 10
        self.helmet_min_samples = 3
        self.helmet_history: Dict[int, Deque[Tuple[int, int]]] = {}

    def reset(self) -> None:
        """Clear all tracks so a new video loop starts with a clean session."""
        self.next_mc_id = 1
        self.motorcycle_tracks.clear()
        self.bbox_to_track_id.clear()
        self.latest_mc_detections = []
        self.helmet_history.clear()

    def _finish_track(self, track_id: int) -> Dict[str, Any]:
        """Remove a track and return its final summary (helmet/rider status snapshotted first)."""
        meta = self.motorcycle_tracks.pop(track_id)
        meta["track_id"] = track_id
        meta["helmet"] = self.get_helmet_status(track_id)
        meta["riders"] = self.get_rider_count(track_id)
        self.helmet_history.pop(track_id, None)
        return meta

    def drain(self) -> List[Dict[str, Any]]:
        """Finish every active track (end of video / shutdown) and return their summaries."""
        finished = [self._finish_track(tid) for tid in list(self.motorcycle_tracks.keys())]
        self.bbox_to_track_id = {}
        self.latest_mc_detections = []
        return finished

    def update_tracks(self, mc_dets: List[Tuple[Tuple[int, int, int, int], float]], now: float) -> List[Dict[str, Any]]:
        """Update active tracks with new detections using greedy nearest-neighbor matching.
        Returns summaries of tracks that left the scene (not seen for max_age_s)."""
        self.latest_mc_detections = mc_dets

        # Remove old tracks that haven't been seen recently
        finished = []
        for track_id in list(self.motorcycle_tracks.keys()):
            meta = self.motorcycle_tracks[track_id]
            if now - meta.get("last_seen", now) > self.max_age_s:
                finished.append(self._finish_track(track_id))

        # Build a cost matrix: (detection_index, track_id) -> distance
        # Use Hungarian-like greedy matching to prevent double-assignment
        unmatched_dets = list(enumerate(mc_dets))  # [(det_idx, (bbox, conf)), ...]
        available_tracks = dict(self.motorcycle_tracks)  # copy so we can remove matched ones
        assigned: Dict[int, Tuple[Tuple[int, int, int, int], float]] = {}  # track_id -> (bbox, conf)

        # Greedy matching: repeatedly pick the closest (det, track) pair
        while unmatched_dets and available_tracks:
            best_det_idx = None
            best_track_id = None
            best_distance = None

            for det_idx, (bbox, conf) in unmatched_dets:
                for track_id, meta in available_tracks.items():
                    prev_bbox = meta.get("bbox")
                    if prev_bbox is None:
                        continue
                    dist = bbox_distance(prev_bbox, bbox)
                    if dist <= self.distance_threshold:
                        if best_distance is None or dist < best_distance:
                            best_distance = dist
                            best_det_idx = det_idx
                            best_track_id = track_id

            if best_track_id is None:
                # No more feasible matches, break early
                break

            # Commit this match
            bbox, conf = next(
                (bbox, conf) for idx, (bbox, conf) in unmatched_dets if idx == best_det_idx
            )
            assigned[best_track_id] = (bbox, conf)
            unmatched_dets = [(i, d) for i, d in unmatched_dets if i != best_det_idx]
            del available_tracks[best_track_id]

        # Create new tracks for unmatched detections
        for _, (bbox, conf) in unmatched_dets:
            new_id = self.next_mc_id
            self.next_mc_id += 1
            assigned[new_id] = (bbox, conf)

        # Update track info for all assigned tracks
        for track_id, (bbox, conf) in assigned.items():
            # Update in place: summary fields (hits, speeds, plate) live for the whole track
            meta = self.motorcycle_tracks.setdefault(track_id, {"first_seen": now, "hits": 0, "conf_sum": 0.0, "speeds": []})
            meta["bbox"] = bbox
            meta["last_seen"] = now
            meta["confidence"] = conf
            meta["hits"] += 1
            meta["conf_sum"] += conf

        # Map current bounding boxes to their assigned track IDs
        self.bbox_to_track_id = {bbox: track_id for track_id, (bbox, _) in assigned.items()}
        return finished

    def record_speed(self, track_id: int, speed: Optional[float]) -> None:
        meta = self.motorcycle_tracks.get(track_id)
        if meta is not None and speed is not None:
            meta["speeds"].append(speed)

    def record_plate(self, track_id: Optional[int], plate_text: str, province_text: str, conf: float) -> None:
        """Keep the most confident plate read for the track's final report."""
        meta = self.motorcycle_tracks.get(track_id) if track_id is not None else None
        if meta is not None and conf >= meta.get("plate_conf", 0.0):
            meta.update(plate_text=plate_text, province_text=province_text, plate_conf=conf)

    def _count_per_track(self, bboxes: List[Tuple[int, int, int, int]]) -> Dict[int, int]:
        """Assign each box to the nearest motorcycle whose rider zone contains its center."""
        counts = {track_id: 0 for track_id in self.bbox_to_track_id.values()}

        for box in bboxes:
            bc = bbox_center(box)
            best_track_id = None
            best_dist = None
            for mc_bbox, track_id in self.bbox_to_track_id.items():
                x1, y1, x2, y2 = mc_bbox
                w, h = x2 - x1, y2 - y1
                # Riders sit above the motorcycle box, so extend the search area upward
                zone = (x1 - 0.2 * w, y1 - 1.0 * h, x2 + 0.2 * w, y2)
                if not point_in_bbox(bc, zone):
                    continue
                dist = bbox_distance(mc_bbox, box)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_track_id = track_id
            if best_track_id is not None:
                counts[best_track_id] += 1
        return counts

    def update_helmets(self, helmet_bboxes: List[Tuple[int, int, int, int]],
                       head_bboxes: List[Tuple[int, int, int, int]]) -> None:
        """Record this frame's helmet and bare-head counts for every tracked motorcycle."""
        helmets = self._count_per_track(helmet_bboxes)
        heads = self._count_per_track(head_bboxes)

        for track_id in helmets:
            hist = self.helmet_history.setdefault(track_id, deque(maxlen=self.helmet_window))
            hist.append((helmets[track_id], heads[track_id]))

    def get_helmet_status(self, track_id: int) -> Optional[Tuple[int, int]]:
        """Smoothed (helmets, bare heads) for a track, or None until enough frames were observed."""
        hist = self.helmet_history.get(track_id)
        if not hist or len(hist) < self.helmet_min_samples:
            return None
        helmets = sorted(h for h, _ in hist)[len(hist) // 2]
        heads = sorted(b for _, b in hist)[len(hist) // 2]
        return helmets, heads

    def get_rider_count(self, track_id: int) -> int:
        """Smoothed number of riders (helmeted + bare heads). A moving motorcycle always has a
        driver, so this is at least 1 even when no head was detected."""
        hist = self.helmet_history.get(track_id)
        if not hist or len(hist) < self.helmet_min_samples:
            return 1
        # Median of per-frame totals, so one frame missing a head doesn't drop the count
        return max(1, sorted(h + b for h, b in hist)[len(hist) // 2])

    def resolve_track_id(self, bbox: Tuple[int, int, int, int]) -> Optional[int]:
        """Resolve a track ID for a given license plate bounding box."""
        if bbox in self.bbox_to_track_id:
            return self.bbox_to_track_id[bbox]

        plate_center = bbox_center(bbox)
        best_inside_id, best_inside_dist = None, None
        best_near_id, best_near_dist = None, None

        # Prefer the nearest motorcycle whose box contains the plate (boxes overlap when bikes
        # are one behind the other); otherwise the nearest one within distance_threshold
        for mc_bbox, _ in self.latest_mc_detections:
            if mc_bbox is None:
                continue
            track_id = self.bbox_to_track_id.get(mc_bbox)
            if track_id is None:
                continue

            dist = bbox_distance(mc_bbox, bbox)
            if point_in_bbox(plate_center, mc_bbox):
                if best_inside_dist is None or dist < best_inside_dist:
                    best_inside_id, best_inside_dist = track_id, dist
            elif dist <= self.distance_threshold:
                if best_near_dist is None or dist < best_near_dist:
                    best_near_id, best_near_dist = track_id, dist

        return best_inside_id if best_inside_id is not None else best_near_id
