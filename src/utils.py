import re
import cv2
from typing import List, Tuple

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
    # Match: (Thai letters/digits){1,4} followed by (digits)
    match = re.match(r"([\d\u0E00-\u0E7F]{2,3})([0-9]*)", plate_text)
    
    if not match:
        return f"{plate_text} {province_text}" if province_text else plate_text
    
    plate_chars = match.group(1)
    digits = match.group(2)
    
    # Validate plate_chars structure
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
    
    # Build formatted string
    if is_valid:
        formatted = plate_chars
        if province_text:
            formatted += f" {province_text}"
        if digits:
            formatted += f" {digits}"
        return formatted
    else:
        # Fallback: return original with province
        return f"{plate_text} {province_text}" if province_text else plate_text


# Fail fast on a stalled RTSP connection instead of OpenCV's default ~30s hang
_RTSP_OPEN_TIMEOUT_MS = 10000
_RTSP_READ_TIMEOUT_MS = 10000


def open_capture(camera_url: str) -> cv2.VideoCapture:
    """Open a cv2 VideoCapture for an RTSP URL or a local video file path."""
    is_rtsp = camera_url.lower().startswith("rtsp://")

    cap = cv2.VideoCapture()
    if is_rtsp:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _RTSP_OPEN_TIMEOUT_MS)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, _RTSP_READ_TIMEOUT_MS)

    if not cap.open(camera_url, cv2.CAP_FFMPEG):
        print(f"[Capture] Failed to open source: {camera_url}")
        return cap

    if is_rtsp:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    return cap


def merge_related_boxes(boxes: List[Tuple[int, int, int, int, float]]) -> List[Tuple[int, int, int, int, float]]:
    """Merge bounding boxes that are close to each other."""
    if not boxes:
        return []

    boxes = sorted(boxes, key=lambda x: x[4], reverse=True)
    bx1, by1, bx2, by2, bconf = boxes[0]

    for x1, y1, x2, y2, _ in boxes[1:]:
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        bcx, bcy = (bx1 + bx2) / 2, (by1 + by2) / 2
        dist = ((cx - bcx) ** 2 + (cy - bcy) ** 2) ** 0.5
        threshold = max(bx2 - bx1, by2 - by1) * 2.5

        if dist < max(150, threshold):
            bx1 = min(bx1, x1)
            by1 = min(by1, y1)
            bx2 = max(bx2, x2)
            by2 = max(by2, y2)

    return [(bx1, by1, bx2, by2, bconf)]


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
