import os
import re
import time
import traceback
from urllib.parse import urlsplit

import cv2
from typing import Dict, List, Tuple


_last_error_log: Dict[str, float] = {}


def log_error(tag: str, exc: BaseException, interval_s: float = 5.0) -> None:
    """Print an error with its traceback, at most once per interval per (tag, error type)
    so a failure repeating inside a worker loop does not flood the console."""
    key = f"{tag}:{type(exc).__name__}"
    now = time.monotonic()
    last = _last_error_log.get(key)
    if last is not None and now - last < interval_s:
        return
    _last_error_log[key] = now
    print(f"[{tag}] {type(exc).__name__}: {exc}")
    traceback.print_exception(type(exc), exc, exc.__traceback__)

def format_thai_plate(plate_text: str, province_text: str) -> str:
    """
    Format Thai motorcycle license plate correctly.
    
    Structure:
    - Line 1: 2-3 Thai letters/digits
      * If 2 chars: only Thai letters (no digits)
      * If 3 chars: 1st can be digit/letter, 2nd-3rd must be Thai letters
    - Line 2: Province name
    - Line 3: 1-4 digits
    """
    plate_text = plate_text.replace(" ", "").strip()
    
    # Split into plate characters and digits
    # Match: an optional digit/letter and 2 Thai letters, followed by (digits). A greedy {2,3} would take
    # the number's first digit into a 2-letter series ("\u0E01\u0E021234" -> "\u0E01\u0E021" + "234")
    match = re.match(r"([\d\u0E00-\u0E7F]?[\u0E00-\u0E7F]{2})([0-9]*)", plate_text)
    
    if not match:
        return f"{plate_text} {province_text}" if province_text else plate_text
    
    plate_chars = match.group(1)
    digits = match.group(2)
    
    is_valid = False
    
    if len(plate_chars) == 2:
        # Must be 2 Thai letters only (no digits)
        if re.match(r"[\u0E00-\u0E7F]{2}", plate_chars):
            is_valid = True
    elif len(plate_chars) >= 3:
        # First char: digit or Thai letter
        # Remaining chars: must be Thai letters
        first_char = plate_chars[0]
        rest_chars = plate_chars[1:]
        
        is_first_valid = bool(re.match(r"[\d\u0E00-\u0E7F]", first_char))
        is_rest_thai = bool(re.match(r"[\u0E00-\u0E7F]+$", rest_chars))
        
        if is_first_valid and is_rest_thai:
            is_valid = True
    
    if is_valid:
        formatted = plate_chars
        if province_text:
            formatted += f" {province_text}"
        if digits:
            formatted += f" {digits}"
        return formatted
    else:
        return f"{plate_text} {province_text}" if province_text else plate_text


# Fail fast on a stalled RTSP connection instead of OpenCV's default ~30s hang
_RTSP_OPEN_TIMEOUT_MS = 10000
_RTSP_READ_TIMEOUT_MS = 10000


def describe_capture_url(camera_url: str) -> str:
    """A capture URL safe to print: network URLs keep only scheme, host and port (the user, password,
    path or query may carry a camera password or token); file paths are kept."""
    parts = urlsplit(camera_url)
    if not parts.netloc:
        return camera_url
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:   # malformed port
        port = ""
    return f"{parts.scheme}://{parts.hostname or '?'}{port}/..."


def open_capture(camera_url: str) -> cv2.VideoCapture:
    """Open a cv2 VideoCapture for an RTSP / http(s) stream URL or a local video file path."""
    is_rtsp = camera_url.lower().startswith(("rtsp://", "rtsps://"))
    is_network = is_rtsp or camera_url.lower().startswith(("http://", "https://"))
    # Low-latency RTSP options; read by OpenCV on every open, and they would break probing of video files
    if is_rtsp:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|analyzeduration;0|probesize;32"
        )
    else:
        os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

    cap = cv2.VideoCapture()
    if is_network:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _RTSP_OPEN_TIMEOUT_MS)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, _RTSP_READ_TIMEOUT_MS)

    if not cap.open(camera_url, cv2.CAP_FFMPEG):
        # Never print the RTSP URL itself: it usually carries the camera password
        print(f"[Capture] Failed to open source: {describe_capture_url(camera_url)}")
        return cap

    if is_rtsp:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    return cap


def _boxes_are_related(a: Tuple[int, int, int, int, float], b: Tuple[int, int, int, int, float]) -> bool:
    """True when two detections likely belong to the same plate (nearby fragments)."""
    dist = bbox_distance(a[:4], b[:4])
    aw, ah = a[2] - a[0], a[3] - a[1]
    bw, bh = b[2] - b[0], b[3] - b[1]
    threshold = max(150.0, max(aw, ah, bw, bh) * 2.5)
    return dist < threshold


def merge_related_boxes(boxes: List[Tuple[int, int, int, int, float]]) -> List[Tuple[int, int, int, int, float]]:
    """Cluster nearby bounding boxes and merge each cluster into one box."""
    if not boxes:
        return []

    n = len(boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            if _boxes_are_related(boxes[i], boxes[j]):
                union(i, j)

    clusters: Dict[int, list] = {}
    for i, box in enumerate(boxes):
        root = find(i)
        x1, y1, x2, y2, conf = box
        if root not in clusters:
            clusters[root] = [x1, y1, x2, y2, conf]
            continue
        c = clusters[root]
        c[0] = min(c[0], x1)
        c[1] = min(c[1], y1)
        c[2] = max(c[2], x2)
        c[3] = max(c[3], y2)
        c[4] = max(c[4], conf)

    return [tuple(c) for c in clusters.values()]


def bbox_center(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Return the center (x, y) of a bounding box."""
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def bbox_distance(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    """Calculate the Euclidean distance between the centers of two bounding boxes."""
    ax, ay = bbox_center(a)
    bx, by = bbox_center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def point_in_bbox(point: Tuple[float, float], bbox: Tuple[float, float, float, float]) -> bool:
    """Check if a point (x, y) is inside a bounding box."""
    x, y = point
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2
