# Speed calibrations saved per video source (SpeedCalibration rows keyed by VideoSource.key).
from typing import Optional

import cv2
import numpy as np

from .models import SpeedCalibration


def saved(source_key: str) -> Optional[SpeedCalibration]:
    return SpeedCalibration.objects.filter(source_key=source_key).first()


def to_dict(calibration: Optional[SpeedCalibration]) -> Optional[dict]:
    # {"points", "width_m", "length_m"} of a saved calibration, or None when there is none.
    if calibration is None or not calibration.points:
        return None
    return {"points": calibration.points, "width_m": calibration.width_m, "length_m": calibration.length_m}


def for_estimator(source_key: str):
    # (points, (width_m, length_m)) to give SpeedEstimator.set_calibration, or None.
    found = to_dict(saved(source_key))
    return ([tuple(p) for p in found["points"]], (found["width_m"], found["length_m"])) if found else None


def reference_frame(source_key: str) -> Optional[np.ndarray]:
    # The frame a video file's points were clicked on (for following a moving camera), or None.
    calibration = saved(source_key)
    if calibration is None or not calibration.points or not calibration.reference_frame:
        return None
    return cv2.imdecode(np.frombuffer(calibration.reference_frame, np.uint8), cv2.IMREAD_COLOR)


def save(source_key: str, points, width_m: float, length_m: float, user, reference_jpeg=None) -> None:
    # reference_jpeg: the frame the points were clicked on; kept for video files only.
    if not source_key.startswith("file:"):
        reference_jpeg = None
    SpeedCalibration.objects.update_or_create(
        source_key=source_key,
        defaults={"points": points, "width_m": width_m, "length_m": length_m, "updated_by": user,
                  "reference_frame": reference_jpeg})


def remove(source_key: str, user) -> None:
    SpeedCalibration.objects.update_or_create(
        source_key=source_key,
        defaults={"points": None, "width_m": None, "length_m": None, "reference_frame": None, "updated_by": user})
