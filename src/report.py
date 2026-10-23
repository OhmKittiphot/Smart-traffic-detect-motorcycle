from typing import Any, Dict


def format_motorcycle_report(meta: Dict[str, Any], speed_calibrated: bool) -> str:
    """Summary printed once a motorcycle has passed (its track ended)."""
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

    speeds = meta.get("speeds") or []
    if not speed_calibrated:
        speed_str = "Calibration required"
    elif not speeds:
        speed_str = "Not measured"
    else:
        # Median of the smoothed readings; robust to a few bad frames at the edges of the view
        speed_str = f"{sorted(speeds)[len(speeds) // 2]:.0f} km/h"

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


def print_motorcycle_reports(finished, speed_calibrated: bool, min_frames: int) -> None:
    for meta in finished:
        # Only report a motorcycle detected together with its rider; short tracks or bikes with no
        # rider detected (parked bikes, false detections) are skipped
        if meta.get("hits", 0) >= min_frames and meta.get("rider_seen"):
            print("\n" + format_motorcycle_report(meta, speed_calibrated) + "\n")
