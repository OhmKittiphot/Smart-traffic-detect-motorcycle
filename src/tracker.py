import time
from typing import Dict, List, Tuple, Any, Optional
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

    def update_tracks(self, mc_dets: List[Tuple[Tuple[int, int, int, int], float]]) -> None:
        """Update active tracks with new detections using greedy nearest-neighbor matching."""
        self.latest_mc_detections = mc_dets
        now = time.time()

        # Remove old tracks that haven't been seen recently
        for track_id in list(self.motorcycle_tracks.keys()):
            meta = self.motorcycle_tracks[track_id]
            if now - meta.get("last_seen", now) > self.max_age_s:
                del self.motorcycle_tracks[track_id]

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
            self.motorcycle_tracks[track_id] = {
                "bbox": bbox,
                "last_seen": now,
                "confidence": conf,
            }

        # Map current bounding boxes to their assigned track IDs
        self.bbox_to_track_id = {bbox: track_id for track_id, (bbox, _) in assigned.items()}

    def resolve_track_id(self, bbox: Tuple[int, int, int, int]) -> Optional[int]:
        """Resolve a track ID for a given license plate bounding box."""
        if bbox in self.bbox_to_track_id:
            return self.bbox_to_track_id[bbox]

        best_track_id = None
        best_score = None

        # Try to find which motorcycle bounding box contains or is closest to this plate
        for mc_bbox, _ in self.latest_mc_detections:
            if mc_bbox is None:
                continue

            if point_in_bbox(bbox_center(bbox), mc_bbox):
                track_id = self.bbox_to_track_id.get(mc_bbox)
                if track_id is not None:
                    return track_id

            dist = bbox_distance(mc_bbox, bbox)
            if best_score is None or dist < best_score:
                best_score = dist
                best_track_id = self.bbox_to_track_id.get(mc_bbox)

        return best_track_id
