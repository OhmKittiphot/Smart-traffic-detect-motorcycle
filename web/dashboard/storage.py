"""Keeps vehicle records and evidence photos within the disk space the dashboard may use.

run.py runs clean_up() at start and then every CLEANUP_EVERY_H hours; `manage.py cleanup` and the
settings page run it by hand. Admins set the limits on the settings page (SystemSettings); they start
from .env (see settings.py):

- RECORD_RETENTION_DAYS: records older than this are deleted with their photos. Confirmed violations are
  kept: they are evidence, and stay until an admin deletes them.
- PHOTO_RETENTION_DAYS: normal motorcycles and rejected events lose their photos after this. The record
  itself (time, plate, counts) stays, so the statistics do not change.
- MEDIA_MAX_MB: if the photos still take more than this, the oldest photos of records that are not
  confirmed violations are removed until they fit in 90% of it.

Photo files no record refers to (e.g. after a record was deleted in Django admin) are removed too.
"""
import logging
import shutil
import threading
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

log = logging.getLogger("dashboard")

PHOTO_FIELDS = ("snapshot", "plate_snapshot")
# A photo file no record refers to is only removed once it is this old (it may be one being saved now)
_ORPHAN_AFTER_S = 3600
# Photos usage is measured at most this often (the system status page asks every few seconds)
_USAGE_CACHE_S = 60
_usage_cache = (0.0, None)


def _media_root() -> Path:
    return Path(settings.MEDIA_ROOT).resolve()


def _photo_path(rel: str):
    """Absolute path of a stored photo, or None for anything outside MEDIA_ROOT."""
    path = (_media_root() / rel).resolve()
    return path if _media_root() in path.parents else None


def delete_photo_files(rel_paths) -> int:
    """Delete these photos (paths relative to MEDIA_ROOT); returns the bytes freed."""
    freed = 0
    for rel in filter(None, rel_paths):
        path = _photo_path(rel)
        if path is not None and path.is_file():
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
    return freed


def remove_empty_day_folders() -> None:
    snap_root = _media_root() / "snapshots"
    if snap_root.is_dir():
        for folder in snap_root.iterdir():
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()


def delete_records(ids) -> int:
    """Delete these vehicle records and their photos; returns how many records were deleted.
    Exactly the given rows go, so records saved meanwhile (and their photos) stay."""
    from .models import VehicleRecord

    ids = list(ids)
    deleted = 0
    for i in range(0, len(ids), 500):   # stay under SQLite's limit on query parameters
        chunk = VehicleRecord.objects.filter(id__in=ids[i:i + 500])
        for photos in chunk.values_list(*PHOTO_FIELDS):
            delete_photo_files(photos)
        deleted += chunk.delete()[0]
    remove_empty_day_folders()
    return deleted


def _drop_photos(records) -> tuple:
    """Delete the photos of these records but keep the records; returns (records changed, bytes freed)."""
    changed = freed = 0
    for record in records:
        freed += delete_photo_files(getattr(record, f) for f in PHOTO_FIELDS)
        record.snapshot = record.plate_snapshot = ""
        record.save(update_fields=list(PHOTO_FIELDS))
        changed += 1
    return changed, freed


def _remove_orphan_photos() -> int:
    """Delete photo files no record refers to; returns the bytes freed."""
    from .models import VehicleRecord

    root = _media_root() / "snapshots"
    if not root.is_dir():
        return 0
    referenced = {p for photos in VehicleRecord.objects.values_list(*PHOTO_FIELDS) for p in photos if p}
    old = time.time() - _ORPHAN_AFTER_S
    freed = 0
    for path in root.rglob("*.jpg"):
        if path.relative_to(_media_root()).as_posix() not in referenced and path.stat().st_mtime < old:
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
    return freed


def photos_bytes() -> int:
    """Total size of the stored photos (measured at most once a minute)."""
    global _usage_cache
    measured_at, size = _usage_cache
    if size is None or time.time() - measured_at > _USAGE_CACHE_S:
        root = _media_root() / "snapshots"
        size = sum(p.stat().st_size for p in root.rglob("*.jpg")) if root.is_dir() else 0
        _usage_cache = (time.time(), size)
    return size


def usage() -> dict:
    """Disk space for the system status page, in bytes."""
    from .models import SystemSettings

    db_path = Path(settings.DATABASES["default"]["NAME"])
    return {
        "photos": photos_bytes(),
        "photos_limit": SystemSettings.load().media_max_mb * 1024 * 1024,
        "database": db_path.stat().st_size if db_path.is_file() else 0,
        "disk_free": shutil.disk_usage(settings.BASE_DIR).free,
    }


def clean_up() -> dict:
    """Apply the retention rules and the size limit once; returns what was removed."""
    global _usage_cache
    from .models import Review, SystemSettings, VehicleRecord

    limits = SystemSettings.load()
    now = timezone.now()
    keep = Q(is_violation=True, review=Review.CONFIRMED)   # evidence: never removed automatically
    with_photos = ~Q(snapshot="") | ~Q(plate_snapshot="")

    # 1. Records past the retention period
    old = VehicleRecord.objects.filter(detected_at__lt=now - timedelta(days=limits.record_retention_days))
    records_deleted = delete_records(old.exclude(keep).values_list("id", flat=True))

    # 2. Photos of normal motorcycles and rejected events past the photo retention period
    photo_cutoff = now - timedelta(days=limits.photo_retention_days)
    not_needed = Q(is_violation=False) | Q(review=Review.REJECTED)
    photos_dropped, freed = _drop_photos(
        VehicleRecord.objects.filter(not_needed, with_photos, detected_at__lt=photo_cutoff))

    # 3. Files left behind without a record
    orphans_freed = _remove_orphan_photos()
    freed += orphans_freed

    # 4. Size limit: oldest photos first, those of normal / rejected records before unreviewed violations,
    #    never those of confirmed violations
    _usage_cache = (0.0, None)
    limit = limits.media_max_mb * 1024 * 1024
    over = photos_bytes() - int(limit * 0.9)
    if photos_bytes() > limit:
        candidates = VehicleRecord.objects.filter(with_photos).exclude(keep).order_by("detected_at")
        for group in (candidates.filter(not_needed), candidates.exclude(not_needed)):
            for record in group.iterator():
                if over <= 0:
                    break
                n, size = _drop_photos([record])
                photos_dropped += n
                freed += size
                over -= size
    remove_empty_day_folders()
    _usage_cache = (0.0, None)

    result = {"records_deleted": records_deleted, "photos_removed": photos_dropped, "bytes_freed": freed}
    if records_deleted or photos_dropped or orphans_freed:
        log.info("Storage clean-up: %d old records deleted, photos of %d records removed (%.1f MB freed).",
                 records_deleted, photos_dropped, freed / 1024 / 1024)
    return result


def start_cleanup_thread() -> None:
    """Run clean_up() now and then every CLEANUP_EVERY_H hours, in the background."""
    def loop():
        while True:
            try:
                clean_up()
            except Exception:
                log.exception("Storage clean-up failed")
            finally:
                close_old_connections()
            time.sleep(settings.CLEANUP_EVERY_H * 3600)

    threading.Thread(target=loop, daemon=True, name="StorageCleanup").start()
