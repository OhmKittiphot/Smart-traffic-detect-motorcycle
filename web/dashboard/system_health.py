# Camera and video file status for the Admin's system status page.
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings

from .sources import list_live_sources, list_video_files

# Cameras are probed over the network, so results are reused for a while instead of on every poll
_CAMERA_CHECK_EVERY_S = 30
_PROBE_TIMEOUT_S = 4
# Some stream servers refuse Python's default User-Agent
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_cache = {"at": 0.0, "cameras": []}
_cache_lock = threading.Lock()


def _probe_hls(url: str) -> tuple:
    # (online, detail) of an HLS stream: its playlist must download and be a playlist.
    try:
        request = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT_S) as response:
            ok = response.status == 200 and response.read(64).lstrip().startswith(b"#EXTM3U")
        return ok, "" if ok else "Unexpected response"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__


def _probe_rtsp(url: str) -> tuple:
    # (online, detail) of an RTSP camera: its port must accept a connection.
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname, parts.port or 554), timeout=_PROBE_TIMEOUT_S):
            return True, ""
    except Exception as e:
        return False, type(e).__name__


def _check_cameras(rtsp_url: str) -> list:
    # (key, label, probe, url); URLs stay on the server, only key / label / result are sent to the page
    targets = [("", "IP camera (RTSP)", _probe_rtsp, rtsp_url)]
    targets += [(key, label, _probe_hls, url) for key, (label, url) in list_live_sources().items()]

    def check(target):
        key, label, probe, url = target
        if not url:
            return {"key": key, "label": label, "state": "unconfigured", "detail": "RTSP_URL not set", "ms": None}
        start = time.perf_counter()
        online, detail = probe(url)
        return {"key": key, "label": label, "state": "online" if online else "offline", "detail": detail,
                "ms": round((time.perf_counter() - start) * 1000) if online else None}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(check, targets))


def cameras(rtsp_url: str) -> tuple:
    # (results, checked_at) of every camera, re-checked at most every _CAMERA_CHECK_EVERY_S seconds.
    with _cache_lock:
        if time.time() - _cache["at"] > _CAMERA_CHECK_EVERY_S:
            _cache["cameras"] = _check_cameras(rtsp_url)
            _cache["at"] = time.time()
        return _cache["cameras"], _cache["at"]


def video_files() -> list:
    # Name, size and last change of each video file the staff can choose (see sources.list_video_files).
    folder = Path(settings.VIDEO_INPUT_DIR)
    files = []
    for name in list_video_files():
        info = (folder / name).stat()
        files.append({"name": name, "size": info.st_size, "modified": info.st_mtime})
    return files
