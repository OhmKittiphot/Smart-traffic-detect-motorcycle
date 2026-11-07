# Saves each motorcycle the detection pipeline reports as a VehicleRecord, with its evidence photos.
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import cv2
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

# A motorcycle of a video file played again is the one saved before when it left view within this many
# seconds of the same time into the clip (frames skipped while the GPU is busy shift it a little)
REPEAT_TOLERANCE_S = 1.0


class ReportRecorder:
    # A report listener (src/report.py). It asks the service for the rules, the source and its settings at
    # each report, since they can change while detection runs.

    def __init__(self, get_config, get_source, get_settings):
        self._get_config = get_config       # -> AppConfig with the current violation rules
        self._get_source = get_source       # -> VideoSource being shown
        self._get_settings = get_settings   # -> SourceSettings of that source
        # One pass = a video file played on from where it was opened or seeked to. Records of the current
        # pass have ids above this (None: a new pass, set at its first report); only earlier passes are
        # looked at for repeats, since two motorcycles side by side in one pass are two motorcycles
        self._pass_after_id = None

    def new_pass(self):
        # The file was opened, seeked, or played to its end: what comes next may be a part played before.
        self._pass_after_id = None

    def __call__(self, meta: dict, speed_calibrated: bool):
        # Called from the processing thread for each motorcycle that left the scene.
        from src.report import find_violations, median_speed

        from .models import VehicleRecord

        config, source, own = self._get_config(), self._get_source(), self._get_settings()
        try:
            if source.is_file:
                if self._pass_after_id is None:
                    self._pass_after_id = VehicleRecord.objects.order_by("-id").values_list("id", flat=True).first() or 0
                if own.skip_repeats and self._saved_before(source, meta):
                    return
            limit = config.speed_limit_kmh if own.speed_limit_kmh is None else own.speed_limit_kmh
            violations = find_violations(meta, speed_calibrated, config.max_riders, limit)
            helmet = meta.get("helmet")
            if helmet == (0, 0):
                helmet = None   # no head seen clearly enough: unknown, same as the console report
            speed = median_speed(meta) if speed_calibrated else None
            VehicleRecord.objects.create(
                detected_at=detected_at(source, meta, own),
                source_mode=source.mode,
                source_name=source.record_name,
                location=own.location if source.is_file else "",
                track_id=meta["track_id"],
                clip_start_s=meta["first_seen"] if source.is_file else None,
                clip_end_s=meta["last_seen"] if source.is_file else None,
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

    def _saved_before(self, source, meta: dict) -> bool:
        # Whether an earlier pass of the file saved this motorcycle: one that left view at about the same
        # time into the clip, and came into view no later (this pass may have started part-way through its
        # time in view, after a seek).
        from .models import VehicleRecord

        start, end = meta["first_seen"], meta["last_seen"]
        return VehicleRecord.objects.filter(
            source_mode="file", source_name=source.video_file, id__lte=self._pass_after_id,
            clip_end_s__range=(end - REPEAT_TOLERANCE_S, end + REPEAT_TOLERANCE_S),
            clip_start_s__lte=start + REPEAT_TOLERANCE_S).exists()


def detected_at(source, meta: dict, settings=None) -> datetime:
    # When the motorcycle passed: for a video file, the file's recording start + its time in the clip;
    # otherwise now. settings: the source's SourceSettings, if already loaded.
    from .source_settings import recording_start

    if source.is_file:
        start, _where = recording_start(source.video_file, settings)
        if start is not None:
            return start + timedelta(seconds=meta["last_seen"])
    return timezone.now()


def save_snapshot(crop) -> str:
    # Save an evidence photo, scaled down to SNAPSHOT_MAX_SIDE to save disk space; returns its path
    # relative to MEDIA_ROOT ('' when there is none).
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
