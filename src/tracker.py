from collections import deque
from typing import Deque, Dict, List, Tuple, Any, Optional
from utils import bbox_distance, point_in_bbox, bbox_center


# Minimum head height relative to the motorcycle box for the head to count as that bike's rider.
# Measured on the test video: real riders 0.18-0.37, heads of riders on bikes behind 0.05-0.07.
MIN_HEAD_TO_BIKE_HEIGHT = 0.12


class MotorcycleTracker:
    """Tracks motorcycles across frames: greedy matching on velocity-predicted position."""

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
        # Riders per frame for each track. A passenger is often hidden behind the driver, so a median
        # undercounts; instead a count is trusted once it was seen in rider_min_frames frames of the window
        self.rider_min_frames = 3
        self.rider_history: Dict[int, Deque[int]] = {}
        self.rider_max: Dict[int, int] = {}
        # Smoothed rider classifier result per track (rider_classifier.summarize); when present it is used
        # instead of the head counts above
        self.rider_summary: Dict[int, Dict[str, Any]] = {}

    def reset(self) -> None:
        """Clear all tracks so a new video loop starts with a clean session."""
        self.next_mc_id = 1
        self.motorcycle_tracks.clear()
        self.bbox_to_track_id.clear()
        self.latest_mc_detections = []
        self.helmet_history.clear()
        self.rider_history.clear()
        self.rider_max.clear()
        self.rider_summary.clear()

    def _finish_track(self, track_id: int) -> Dict[str, Any]:
        """Remove a track and return its final summary (helmet/rider status snapshotted first)."""
        meta = self.motorcycle_tracks.pop(track_id)
        meta["track_id"] = track_id
        meta["helmet"] = self.get_helmet_status(track_id)
        meta["riders"] = self.get_rider_count(track_id)
        # A rider's head was detected on this bike in at least rider_min_frames frames
        # (or the rider classifier gave a result for it)
        meta["rider_seen"] = self.rider_max.get(track_id, 0) > 0 or track_id in self.rider_summary
        self.helmet_history.pop(track_id, None)
        self.rider_history.pop(track_id, None)
        self.rider_max.pop(track_id, None)
        self.rider_summary.pop(track_id, None)
        return meta

    def drain(self) -> List[Dict[str, Any]]:
        """Finish every active track (end of video / shutdown) and return their summaries."""
        finished = [self._finish_track(tid) for tid in list(self.motorcycle_tracks.keys())]
        self.bbox_to_track_id = {}
        self.latest_mc_detections = []
        return finished

    @staticmethod
    def _dedupe_detections(mc_dets):
        """Drop duplicate boxes on one bike (YOLO sometimes returns two overlapping boxes per bike,
        which would otherwise become two tracks and two reports). Highest confidence wins."""
        kept = []
        for bbox, conf in sorted(mc_dets, key=lambda d: d[1], reverse=True):
            x1, y1, x2, y2 = bbox
            area = max(1, (x2 - x1) * (y2 - y1))
            dup = False
            for (kx1, ky1, kx2, ky2), _ in kept:
                iw = min(x2, kx2) - max(x1, kx1)
                ih = min(y2, ky2) - max(y1, ky1)
                if iw <= 0 or ih <= 0:
                    continue
                inter = iw * ih
                k_area = max(1, (kx2 - kx1) * (ky2 - ky1))
                # Overlapping a lot, or one box mostly inside the other
                if inter / (area + k_area - inter) > 0.45 or inter / min(area, k_area) > 0.75:
                    dup = True
                    break
            if not dup:
                kept.append((bbox, conf))
        return kept

    def _match_cost(self, meta, bbox, now: float) -> Optional[float]:
        """Cost of assigning a detection to a track, or None if it can't be the same bike.
        Compares against the position predicted from the track's velocity, scaled to the bike's size,
        so a stale track left behind by another bike can't steal its detection."""
        px1, py1, px2, py2 = meta["bbox"]
        dt = max(0.0, now - meta["last_seen"])
        horizon = min(dt, 1.0)  # don't extrapolate far: bikes turn and stop
        vx, vy = meta.get("vel", (0.0, 0.0))
        pcx = (px1 + px2) / 2.0 + vx * horizon
        pcy = (py1 + py2) / 2.0 + vy * horizon

        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        dist = ((cx - pcx) ** 2 + (cy - pcy) ** 2) ** 0.5

        ph, h = max(1, py2 - py1), max(1, y2 - y1)
        if not 0.5 <= h / ph <= 2.0:
            return None  # far too different in size to be the same bike

        diag = ((px2 - px1) ** 2 + ph ** 2) ** 0.5
        # Allowed error grows with the bike's size and with how long the track went unseen
        gate = min(self.distance_threshold, max(40.0, diag * (0.6 + horizon)))
        if dist > gate:
            return None
        # Prefer recently seen tracks when two are similarly close
        return dist / gate + 0.5 * horizon

    def update_tracks(self, mc_dets: List[Tuple[Tuple[int, int, int, int], float]], now: float) -> List[Dict[str, Any]]:
        """Update active tracks with new detections using greedy matching on predicted position.
        Returns summaries of tracks that left the scene (not seen for max_age_s)."""
        mc_dets = self._dedupe_detections(mc_dets)
        self.latest_mc_detections = mc_dets

        # Remove old tracks that haven't been seen recently
        finished = []
        for track_id in list(self.motorcycle_tracks.keys()):
            meta = self.motorcycle_tracks[track_id]
            if now - meta.get("last_seen", now) > self.max_age_s:
                finished.append(self._finish_track(track_id))

        # Greedy matching: all feasible (det, track) pairs, cheapest first, each used once
        pairs = []
        for det_idx, (bbox, _) in enumerate(mc_dets):
            for track_id, meta in self.motorcycle_tracks.items():
                cost = self._match_cost(meta, bbox, now)
                if cost is not None:
                    pairs.append((cost, det_idx, track_id))
        pairs.sort()

        assigned: Dict[int, Tuple[Tuple[int, int, int, int], float]] = {}  # track_id -> (bbox, conf)
        used_dets = set()
        for _, det_idx, track_id in pairs:
            if det_idx in used_dets or track_id in assigned:
                continue
            assigned[track_id] = mc_dets[det_idx]
            used_dets.add(det_idx)

        # Create new tracks for unmatched detections
        for det_idx, det in enumerate(mc_dets):
            if det_idx not in used_dets:
                assigned[self.next_mc_id] = det
                self.next_mc_id += 1

        # Update track info for all assigned tracks
        for track_id, (bbox, conf) in assigned.items():
            # Update in place: summary fields (hits, speeds, plate) live for the whole track
            meta = self.motorcycle_tracks.setdefault(track_id, {"first_seen": now, "hits": 0, "speeds": []})
            prev, prev_t = meta.get("bbox"), meta.get("last_seen")
            if prev is not None and now > prev_t:
                (ox, oy), (nx, ny) = bbox_center(prev), bbox_center(bbox)
                dt = now - prev_t
                v_new = ((nx - ox) / dt, (ny - oy) / dt)
                vx, vy = meta.get("vel", v_new)
                meta["vel"] = (0.5 * vx + 0.5 * v_new[0], 0.5 * vy + 0.5 * v_new[1])
            meta["bbox"] = bbox
            meta["last_seen"] = now
            meta["confidence"] = conf
            meta["hits"] += 1

        # Map current bounding boxes to their assigned track IDs
        self.bbox_to_track_id = {bbox: track_id for track_id, (bbox, _) in assigned.items()}
        return finished

    def record_snapshot(self, track_id: int, frame, bbox: Tuple[int, int, int, int]) -> None:
        """Keep a crop of the motorcycle and its riders from the frame where it looks biggest
        (usually closest to the camera); used as evidence in the web dashboard."""
        meta = self.motorcycle_tracks.get(track_id)
        if meta is None:
            return
        x1, y1, x2, y2 = bbox
        area = (x2 - x1) * (y2 - y1)
        if area <= meta.get("snapshot_area", 0):
            return
        h, w = frame.shape[:2]
        bw, bh = x2 - x1, y2 - y1
        # Riders sit above the motorcycle box (same zone as the rider count)
        crop = frame[max(0, int(y1 - bh)):min(h, y2), max(0, int(x1 - 0.1 * bw)):min(w, int(x2 + 0.1 * bw))]
        if crop.size:
            meta["snapshot"] = crop.copy()
            meta["snapshot_area"] = area

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
                # Heads of the same bike sit within its width; a wide margin grabs heads from the bike behind
                zone = (x1 - 0.05 * w, y1 - 1.0 * h, x2 + 0.05 * w, y2)
                if not point_in_bbox(bc, zone):
                    continue
                # A rider's head is ~0.18-0.37 of the bike's height; a much smaller head inside the zone
                # belongs to a rider on a bike further back
                if (box[3] - box[1]) < MIN_HEAD_TO_BIKE_HEIGHT * h:
                    continue
                dist = bbox_distance(mc_bbox, box)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_track_id = track_id
            if best_track_id is not None:
                counts[best_track_id] += 1
        return counts

    def update_helmets(self, helmet_bboxes: List[Tuple[int, int, int, int]],
                       head_bboxes: List[Tuple[int, int, int, int]],
                       rider_bboxes: Optional[List[Tuple[int, int, int, int]]] = None) -> None:
        """Record this frame's helmet / bare-head counts and rider count for every tracked motorcycle.
        rider_bboxes are all heads (helmeted or not) kept at the lower rider_conf threshold."""
        helmets = self._count_per_track(helmet_bboxes)
        heads = self._count_per_track(head_bboxes)
        riders = self._count_per_track(rider_bboxes) if rider_bboxes is not None else {
            tid: helmets[tid] + heads[tid] for tid in helmets}

        for track_id in helmets:
            hist = self.helmet_history.setdefault(track_id, deque(maxlen=self.helmet_window))
            hist.append((helmets[track_id], heads[track_id]))

            r_hist = self.rider_history.setdefault(track_id, deque(maxlen=self.helmet_window))
            r_hist.append(riders[track_id])
            if len(r_hist) >= self.rider_min_frames:
                # Highest count reached in at least rider_min_frames frames of the window
                est = sorted(r_hist, reverse=True)[self.rider_min_frames - 1]
                self.rider_max[track_id] = max(self.rider_max.get(track_id, 0), est)

    def update_rider_summary(self, track_id: int, summary: Optional[Dict[str, Any]]) -> None:
        """Store the rider classifier's smoothed result for a track (None = not enough frames yet)."""
        if summary is not None and track_id in self.motorcycle_tracks:
            self.rider_summary[track_id] = summary

    def get_helmet_status(self, track_id: int) -> Optional[Tuple[int, int]]:
        """Smoothed (helmets, bare heads) for a track, or None until enough frames were observed."""
        summary = self.rider_summary.get(track_id)
        if summary is not None:
            return summary["riders"] - summary["no_helmet"], summary["no_helmet"]
        hist = self.helmet_history.get(track_id)
        if not hist or len(hist) < self.helmet_min_samples:
            return None
        helmets = sorted(h for h, _ in hist)[len(hist) // 2]
        heads = sorted(b for _, b in hist)[len(hist) // 2]
        return helmets, heads

    def get_rider_count(self, track_id: int) -> int:
        """Most riders seen on this track so far (counted as above). A moving motorcycle always has a
        driver, so this is at least 1 even when no head was detected."""
        summary = self.rider_summary.get(track_id)
        if summary is not None:
            return max(1, summary["riders"])
        return max(1, self.rider_max.get(track_id, 0))

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
