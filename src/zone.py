from typing import List, Optional, Tuple

import cv2
import numpy as np


class DetectionZone:
    """The area of the frame motorcycles are detected in, e.g. only the lanes facing the camera: motorcycles
    outside it (parked, far lanes too small to judge, the other side of the road) are not tracked, counted
    or reported. A motorcycle is inside when the middle of its box's bottom edge, where it touches the road,
    is inside. No points: the whole frame."""

    def __init__(self):
        self._polygon: Optional[np.ndarray] = None

    def set_points(self, points: Optional[List[Tuple[float, float]]]) -> None:
        """Corners of the area in frame pixels, in order around it (at least 3); None or [] = the whole frame."""
        self._polygon = np.array(points, dtype=np.float32).reshape(-1, 1, 2) if points else None

    def points(self) -> Optional[List[Tuple[float, float]]]:
        return None if self._polygon is None else [tuple(p) for p in self._polygon.reshape(-1, 2).tolist()]

    def keep(self, mc_bboxes: list) -> list:
        """The [(box, conf)] of motorcycles inside the area."""
        if self._polygon is None:
            return mc_bboxes
        return [(box, conf) for box, conf in mc_bboxes
                if cv2.pointPolygonTest(self._polygon, ((box[0] + box[2]) / 2, float(box[3])), False) >= 0]
