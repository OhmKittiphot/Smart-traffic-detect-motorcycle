from typing import Any, Callable, Dict, List, Optional

from .utils import log


# Called with (meta, speed_calibrated) for every reported motorcycle, e.g. to store it in the web dashboard
_report_listeners: List[Callable[[Dict[str, Any], bool], None]] = []


def add_report_listener(fn: Callable[[Dict[str, Any], bool], None]):
    _report_listeners.append(fn)


def remove_report_listener(fn: Callable[[Dict[str, Any], bool], None]):
    if fn in _report_listeners:
        _report_listeners.remove(fn)


def median_speed(meta: Dict[str, Any]) -> Optional[float]:
    # Median of the smoothed readings; robust to a few bad frames at the edges of the view.
    speeds = meta.get("speeds") or []
    return sorted(speeds)[len(speeds) // 2] if speeds else None


def find_violations(meta: Dict[str, Any], speed_calibrated: bool, max_riders: int,
                    speed_limit_kmh: float) -> List[str]:
    # Violation codes for a finished motorcycle: NO_HELMET, OVERLOAD, SPEEDING.
    violations = []
    helmet = meta.get("helmet")
    if helmet is not None and helmet[1] > 0:
        violations.append("NO_HELMET")
    if (meta.get("riders") or 0) > max_riders:
        violations.append("OVERLOAD")
    speed = median_speed(meta) if speed_calibrated else None
    if speed is not None and speed_limit_kmh > 0 and speed > speed_limit_kmh:
        violations.append("SPEEDING")
    return violations


def format_motorcycle_report(meta: Dict[str, Any], speed_calibrated: bool) -> str:
    # Summary printed once a motorcycle has passed (its track ended).
    if meta.get("plate_text"):
        plate_str = f"{meta['plate_text']} {meta.get('province_text') or 'Province not found'}"
    else:
        plate_str = "License plate not found"

    helmet = meta.get("helmet")
    if helmet is None or helmet == (0, 0):
        helmet_str = "Undetermined"
    else:
        helmet_str = f"Helmet x{helmet[0]}, No helmet x{helmet[1]}"

    riders = meta.get("riders")
    riders_str = str(riders) if riders else "Undetermined"

    speed = median_speed(meta)
    if not speed_calibrated:
        speed_str = "Calibration required"
    elif speed is None:
        speed_str = "Not measured"
    else:
        speed_str = f"{speed:.0f} km/h"

    plate_conf = meta.get("plate_conf")
    conf_str = f"{plate_conf * 100:.1f}%" if plate_conf else "N/A"

    return "\n".join([
        "=========================================",
        f"Motorcycle ID          : {meta['track_id']}",
        f"License Plate Detected : {plate_str}",
        f"Helmet                 : {helmet_str}",
        f"Riders                 : {riders_str}",
        f"Speed                  : {speed_str}",
        f"Confidence             : {conf_str}",
        "=========================================",
    ])


def print_motorcycle_reports(finished, speed_calibrated: bool, min_frames: int):
    for meta in finished:
        # Only report a motorcycle detected together with its rider; short tracks or bikes with no
        # rider detected (parked bikes, false detections) are skipped
        if meta.get("hits", 0) >= min_frames and meta.get("rider_seen"):
            log.info("\n%s\n", format_motorcycle_report(meta, speed_calibrated))
            for fn in _report_listeners:
                try:
                    fn(meta, speed_calibrated)
                except Exception:
                    log.exception("[Report] Listener failed")
