import math
from collections import deque
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


def parse_calibration(points_str: str, world_str: str):
    # Parse 'x,y;x,y;x,y;x,y' and 'W,L' (meters). Returns (points, (W, L)) or None.
    try:
        pts = [tuple(float(v) for v in p.split(",")) for p in points_str.split(";") if p.strip()]
        w, l = (float(v) for v in world_str.split(","))
        if len(pts) != 4 or any(len(p) != 2 for p in pts) or w <= 0 or l <= 0:
            return None
        return pts, (w, l)
    except Exception:
        return None


class SpeedEstimator:
    # Speed from a homography to road-plane meters: arc length of the smoothed track over a sliding window,
    # so curved roads work too.

    def __init__(self, window_s: float = 1.0, min_span_s: float = 0.4,
                 smooth_n: int = 3, max_kmh: float = 200.0, still_dist_m: float = 0.3):
        self.window_s = window_s
        self.min_span_s = min_span_s
        self.smooth_n = smooth_n
        self.max_kmh = max_kmh
        self.still_dist_m = still_dist_m
        self._H: Optional[np.ndarray] = None       # current image -> road plane (metres)
        self._H_ref: Optional[np.ndarray] = None   # the frame the points were clicked on -> road plane
        self._ref_pts: Optional[np.ndarray] = None
        self._motion: Optional[np.ndarray] = None  # camera motion, clicked frame -> current (None: still)
        self._tracks: Dict[int, dict] = {}

    @property
    def calibrated(self) -> bool:
        return self._H is not None

    def set_calibration(self, image_pts: List[Tuple[float, float]], world_size: Tuple[float, float]):
        # image_pts order: far-left, far-right, near-right, near-left of a ground rectangle.
        # world_size = (width_m between pts 1-2, length_m between pts 2-3).
        w, l = world_size
        src = np.array(image_pts, dtype=np.float32)
        if len(src) != 4 or not cv2.isContourConvex(src) or cv2.contourArea(src) < 100:
            raise ValueError("points must form a convex quadrilateral, in order")
        dst = np.array([[0, 0], [w, 0], [w, l], [0, l]], dtype=np.float32)
        H = cv2.getPerspectiveTransform(src, dst)
        if not np.isfinite(H).all():
            raise ValueError("degenerate points")
        self._H = self._H_ref = H
        self._ref_pts = src
        self._motion = None
        self._tracks = {}

    def set_camera_motion(self, motion: Optional[np.ndarray]):
        # The camera moved: motion maps the frame the points were clicked on onto the current frame
        # (from CameraMotion). Road positions stay in the same metric frame, so speeds are not disturbed.
        if self._H_ref is None or motion is None:
            return
        self._motion = motion
        self._H = self._H_ref @ np.linalg.inv(motion)

    def image_points(self) -> Optional[List[Tuple[float, float]]]:
        # The 4 calibration points where they are on the current frame (they move with the camera).
        if self._ref_pts is None:
            return None
        if self._motion is None:
            return [tuple(map(float, p)) for p in self._ref_pts]
        moved = cv2.perspectiveTransform(self._ref_pts.reshape(-1, 1, 2), self._motion)
        return [tuple(map(float, p)) for p in moved.reshape(-1, 2)]

    def _to_world(self, x: float, y: float) -> Tuple[float, float]:
        p = cv2.perspectiveTransform(np.array([[[x, y]]], dtype=np.float32), self._H)
        return float(p[0, 0, 0]), float(p[0, 0, 1])

    def update(self, track_id: int, bbox: Tuple[int, int, int, int], t: float) -> Optional[float]:
        # Add an observation (bbox bottom-center = ground contact) and return speed in km/h.
        if self._H is None:
            return None
        x1, _, x2, y2 = bbox
        wx, wy = self._to_world((x1 + x2) / 2.0, float(y2))

        st = self._tracks.get(track_id)
        # The same moment again (a repeated frame) adds nothing; restarting would throw the history away
        if st is not None and t == st["last_t"]:
            return st["speed"]
        # Time going backwards means the video looped: restart this track's history
        if st is None or t < st["last_t"]:
            st = {"raw": deque(maxlen=self.smooth_n), "hist": deque(), "speed": None, "last_t": t}
            self._tracks[track_id] = st
        st["last_t"] = t

        st["raw"].append((wx, wy))
        sx = sum(p[0] for p in st["raw"]) / len(st["raw"])
        sy = sum(p[1] for p in st["raw"]) / len(st["raw"])
        hist = st["hist"]
        hist.append((t, sx, sy))
        while hist and t - hist[0][0] > self.window_s:
            hist.popleft()

        span = hist[-1][0] - hist[0][0]
        if len(hist) < 2 or span < self.min_span_s:
            return st["speed"]

        path = sum(math.hypot(hist[i][1] - hist[i - 1][1], hist[i][2] - hist[i - 1][2])
                   for i in range(1, len(hist)))
        net = math.hypot(hist[-1][1] - hist[0][1], hist[-1][2] - hist[0][2])
        if net < self.still_dist_m:
            path = 0.0

        kmh = path / span * 3.6
        if kmh > self.max_kmh:
            return st["speed"]
        st["speed"] = kmh if st["speed"] is None else 0.6 * st["speed"] + 0.4 * kmh
        return st["speed"]

    def get_speed(self, track_id: int) -> Optional[float]:
        st = self._tracks.get(track_id)
        return st["speed"] if st else None

    def clear_calibration(self):
        # Forget the calibration; speeds are not measured until it is set again.
        self._H = self._H_ref = self._ref_pts = self._motion = None
        self._tracks = {}

    def reset(self):
        # Drop per-track speed history; keep the homography calibration.
        self._tracks = {}

    def prune(self, now_t: float, max_age_s: float = 3.0):
        for tid in list(self._tracks.keys()):
            if abs(now_t - self._tracks[tid]["last_t"]) > max_age_s:
                del self._tracks[tid]
