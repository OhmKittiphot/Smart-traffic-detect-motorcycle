"""Saves each motorcycle the detection pipeline reports as a VehicleRecord, with its evidence photos."""
import uuid
from datetime import datetime
from pathlib import Path

import cv2
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone


class ReportRecorder:
    """A report listener (src/report.py). It asks the service for the rules and the source at each report,
    since both can change while detection runs."""

    def __init__(self, get_config, get_source):
        self._get_config = get_config   # -> AppConfig with the current violation rules
        self._get_source = get_source   # -> VideoSource being shown

    def __call__(self, meta: dict, speed_calibrated: bool) -> None:
        """Called from the processing thread for each motorcycle that left the scene."""
        from report import find_violations, median_speed

        from .models import VehicleRecord

        config, source = self._get_config(), self._get_source()
        try:
            violations = find_violations(meta, speed_calibrated, config.max_riders, config.speed_limit_kmh)
            helmet = meta.get("helmet")
            if helmet == (0, 0):
                helmet = None   # no head seen clearly enough: unknown, same as the console report
            speed = median_speed(meta) if speed_calibrated else None
            VehicleRecord.objects.create(
                detected_at=timezone.now(),
                source_mode=source.mode,
                source_name=source.record_name,
                track_id=meta["track_id"],
                plate_text=(meta.get("plate_text") or "")[:32],
                province_text=(meta.get("province_text") or "")[:64],
                plate_conf=meta.get("plate_conf"),
                riders=meta.get("riders"),
                helmet_count=helmet[0] if helmet else None,
                no_helmet_count=helmet[1] if helmet else None,
                speed_kmh=round(speed, 1) if speed is not None else None,
                violations=",".join(violations),
                is_violation=bool(violations),
                snapshot=save_snapshot(meta.get("snapshot")),
                plate_snapshot=save_snapshot(meta.get("plate_snapshot")),
            )
        finally:
            close_old_connections()


def save_snapshot(crop) -> str:
    """Save an evidence photo, scaled down to SNAPSHOT_MAX_SIDE to save disk space; returns its path
    relative to MEDIA_ROOT ('' when there is none)."""
    if crop is None or not getattr(crop, "size", 0):
        return ""
    h, w = crop.shape[:2]
    scale = settings.SNAPSHOT_MAX_SIDE / max(h, w)
    if scale < 1:
        crop = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, settings.SNAPSHOT_JPEG_QUALITY])
    if not ok:
        return ""
    rel = Path("snapshots") / datetime.now().strftime("%Y%m%d") / f"{uuid.uuid4().hex}.jpg"
    dest = Path(settings.MEDIA_ROOT) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.tobytes())
    return rel.as_posix()
