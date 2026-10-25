import re
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Optional

# A live playlist gains a segment every TARGETDURATION seconds; one unchanged for this long has stopped
# (the server keeps serving its last segments, so a player would replay the same few seconds forever)
STALE_AFTER_S = 30.0


def is_hls_url(url: str) -> bool:
    return url.lower().startswith(("http://", "https://")) and ".m3u8" in url.lower()


def playlist_age_s(url: str, timeout: float = 5.0) -> Optional[float]:
    """Seconds since a live HLS playlist last changed, from the server's own Date and Last-Modified headers
    (no clock skew with this machine). None when it can't be told: no headers, request failed, or not a
    media playlist."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            last_modified, date = resp.headers.get("Last-Modified"), resp.headers.get("Date")
            body = resp.read(65536).decode("utf-8", errors="replace")
    except Exception:
        return None
    # A master playlist only lists variant streams; its own timestamp says nothing about the video
    if "#EXT-X-MEDIA-SEQUENCE" not in body and not re.search(r"^#EXTINF", body, re.M):
        return None
    if "#EXT-X-ENDLIST" in body:
        return None   # recorded video, not live
    if not last_modified or not date:
        return None
    try:
        return max(0.0, (parsedate_to_datetime(date) - parsedate_to_datetime(last_modified)).total_seconds())
    except (TypeError, ValueError):
        return None


def format_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return f"{int(seconds)}s"
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"
