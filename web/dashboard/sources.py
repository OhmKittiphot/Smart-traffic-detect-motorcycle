# Video sources: files in VIDEO_INPUT_DIR, the IP camera (RTSP_URL) and add-on cameras.
# Requests only name a source; URLs come from .env and add-ons, so a request cannot open any path or URL.
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import numpy as np
from django.conf import settings
from django.utils.translation import gettext as _

# The IP camera's key part in a source key ("live:ip")
IP_CAMERA = "ip"


def list_video_files() -> list:
    # Video file names directly inside VIDEO_INPUT_DIR (no sub-folders, no paths).
    folder = Path(settings.VIDEO_INPUT_DIR)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in settings.VIDEO_EXTENSIONS)


def resolve_video_file(name: str) -> Optional[Path]:
    # Path of an allowed video file, or None. Only names returned by list_video_files() are accepted,
    # so a crafted name (../, absolute path, other drive) can never reach the file system.
    if not name or name not in list_video_files():
        return None
    return Path(settings.VIDEO_INPUT_DIR) / name


def list_live_sources() -> dict:
    # key -> (label, url) of live sources besides the IP camera, from optional add-ons (itic_cameras.py).
    # The IP camera itself is key ''.
    try:
        from . import itic_cameras
    except ImportError:
        return {}
    return itic_cameras.live_sources()


def rtsp_url() -> str:
    # The IP camera's URL from .env ('' when not configured).
    from src.app_config import AppConfig

    return AppConfig.from_env().rtsp_url


@dataclass(frozen=True)
class VideoSource:
    # One video source, as the dashboard chooses it.
    mode: str = "live"      # "live" or "file" (SourceMode)
    video_file: str = ""    # mode "file": a name from list_video_files(), the one playing
    live_key: str = ""      # mode "live": '' = IP camera, else a key of list_live_sources()
    # mode "file": files played one after another, video_file among them; () = video_file alone
    playlist: tuple = ()

    @classmethod
    def from_key(cls, source_key: str) -> "VideoSource":
        # The source of a key from .key: "file:<name>", "live:ip" or "live:<add-on key>".
        kind, _, name = source_key.partition(":")
        if kind == "file":
            return cls("file", video_file=name)
        return cls("live", live_key="" if name == IP_CAMERA else name)

    @property
    def is_file(self) -> bool:
        return self.mode == "file"

    @property
    def key(self) -> str:
        # Identifies the source's camera view, e.g. for its speed calibration.
        return f"file:{self.video_file}" if self.is_file else f"live:{self.live_key or IP_CAMERA}"

    @property
    def live_label(self) -> str:
        # Label of an add-on camera; '' for the IP camera and files.
        extra = list_live_sources().get(self.live_key) if self.live_key else None
        return extra[0] if extra else ""

    @property
    def record_name(self) -> str:
        # Saved as VehicleRecord.source_name: the file name, the add-on camera's label, '' for the IP camera.
        return self.video_file if self.is_file else self.live_label

    def next_in_playlist(self) -> Optional["VideoSource"]:
        # The same playlist on its file after this one (the first after the last), or None without one.
        if self.video_file not in self.playlist:
            return None
        following = (self.playlist.index(self.video_file) + 1) % len(self.playlist)
        return replace(self, video_file=self.playlist[following])

    def describe(self) -> str:
        if self.video_file in self.playlist:
            number = self.playlist.index(self.video_file) + 1
            return f"File: {self.video_file} ({number}/{len(self.playlist)})"
        if self.is_file:
            return f"File: {self.video_file}"
        return f"Live: {self.live_label}" if self.live_key else "Live camera (RTSP)"

    def capture_url(self, ip_camera_url: str) -> Optional[str]:
        # URL or file path to open; '' for the IP camera when it is not configured, None when the file or
        # add-on camera does not exist (any more).
        if self.is_file:
            path = resolve_video_file(self.video_file)
            return str(path) if path else None
        if self.live_key:
            extra = list_live_sources().get(self.live_key)
            return extra[1] if extra else None
        return ip_camera_url or ""

    def is_configured(self, ip_camera_url: str) -> bool:
        # False only for the IP camera without RTSP_URL.
        return self.is_file or bool(self.live_key or ip_camera_url)


def source_keys() -> set:
    # Keys of every video source that can have a speed calibration (see VideoSource.key).
    return ({f"live:{IP_CAMERA}"} | {f"live:{key}" for key in list_live_sources()}
            | {f"file:{name}" for name in list_video_files()})


def grab_frame(source_key: str) -> np.ndarray:
    # One raw frame of a source, opened on its own, so a calibration can be drawn while detection is off.
    # It is the same full-size frame detection reads. Raises RuntimeError when the source sends nothing.
    from src.utils import open_capture

    url = VideoSource.from_key(source_key).capture_url(rtsp_url())
    if not url:
        raise RuntimeError(_("This video source is not configured"))
    cap = open_capture(url)
    try:
        ok, frame = cap.read() if cap.isOpened() else (False, None)
    finally:
        cap.release()
    if not ok or frame is None:
        raise RuntimeError(_("No video signal from this source"))
    return frame
