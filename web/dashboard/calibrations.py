"""Speed calibrations saved per video source (SpeedCalibration rows, keyed by VideoSource.key).

A row with points = None means the calibration was removed on purpose: it is kept so the IP camera's
.env fallback (SPEED_CALIB_POINTS / SPEED_CALIB_WORLD) is not applied again after a restart.
"""
from typing import Optional

from .models import SpeedCalibration
from .sources import IP_CAMERA

_IP_CAMERA_KEY = f"live:{IP_CAMERA}"


def saved(source_key: str) -> Optional[SpeedCalibration]:
    return SpeedCalibration.objects.filter(source_key=source_key).first()


def to_dict(calibration: Optional[SpeedCalibration]) -> Optional[dict]:
    """{"points", "width_m", "length_m"} of a saved calibration, or None when there is none."""
    if calibration is None or not calibration.points:
        return None
    return {"points": calibration.points, "width_m": calibration.width_m, "length_m": calibration.length_m}


def uses_env_fallback(source_key: str, calibration: Optional[SpeedCalibration], config) -> bool:
    """The IP camera uses the calibration in .env until one is saved (or removed) from the dashboard."""
    return source_key == _IP_CAMERA_KEY and calibration is None and bool(config.speed_calib_points)


def for_estimator(source_key: str, config):
    """(points, (width_m, length_m)) to give SpeedEstimator.set_calibration, or None."""
    from speed import parse_calibration

    calibration = saved(source_key)
    if uses_env_fallback(source_key, calibration, config):
        return parse_calibration(config.speed_calib_points, config.speed_calib_world)
    found = to_dict(calibration)
    return ([tuple(p) for p in found["points"]], (found["width_m"], found["length_m"])) if found else None


def save(source_key: str, points, width_m: float, length_m: float, user) -> None:
    SpeedCalibration.objects.update_or_create(
        source_key=source_key,
        defaults={"points": points, "width_m": width_m, "length_m": length_m, "updated_by": user})


def remove(source_key: str, user) -> None:
    SpeedCalibration.objects.update_or_create(
        source_key=source_key, defaults={"points": None, "width_m": None, "length_m": None, "updated_by": user})
