# Per-source settings from the live view's menu (SourceSettings rows keyed by VideoSource.key):
# speed limit, detection zone, and for files when/where filmed and whether replays save again.
# Recording time: the menu's value, else the time stored in the file, else unknown (saved at play time).
from datetime import datetime
from functools import lru_cache
from typing import Optional, Tuple

import av

from .models import SourceSettings
from .sources import resolve_video_file

# Metadata entries with the time recording started, best first: Apple's own entry keeps the camera's time
# zone, creation_time is in UTC
_METADATA_KEYS = ("com.apple.quicktime.creationdate", "creation_time")


def load(source_key: str) -> SourceSettings:
    # The source's settings; the defaults (not saved) when none were set.
    return SourceSettings.objects.filter(source_key=source_key).first() or SourceSettings(source_key=source_key)


@lru_cache(maxsize=64)
def _time_in_file(path: str, _modified: float) -> Optional[datetime]:
    # The recording time in a file's metadata (cached; _modified makes a replaced file be read again).
    try:
        with av.open(path) as container:
            metadata = dict(container.metadata)
    except (av.FFmpegError, OSError):
        return None
    for key in _METADATA_KEYS:
        try:
            recorded = datetime.fromisoformat(metadata.get(key, ""))
        except ValueError:
            continue
        if recorded.tzinfo is not None:   # without a time zone it could be any
            return recorded
    return None


def time_in_file(name: str) -> Optional[datetime]:
    # When the camera says a video file (a name inside VIDEO_INPUT_DIR) started recording, if it says.
    path = resolve_video_file(name)
    return _time_in_file(str(path), path.stat().st_mtime) if path else None


def recording_start(name: str, settings: Optional[SourceSettings] = None) -> Tuple[Optional[datetime], str]:
    # (when a video file started recording, where that comes from: "set" in the menu, "file" for its
    # metadata, "" when unknown). settings: the file's, when already loaded.
    settings = settings or load(f"file:{name}")
    if settings.recorded_at is not None:
        return settings.recorded_at, "set"
    in_file = time_in_file(name)
    return (in_file, "file") if in_file else (None, "")
