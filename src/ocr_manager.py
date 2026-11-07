import math
from typing import Dict, List, Tuple, Any, Optional

from .ocr import is_valid_number, is_valid_series
from .ocr_voting import vote_line, vote_temporal_strings


class OCRManager:
    # Manages OCR caching, history, and voting for license plates, bound to track_id.

    def __init__(
        self,
        cache_ttl_s: float = 5.0,
        cache_distance: float = 120.0,
        lock_stable_count: int = 5,
        ocr_request_interval_s: float = 0.1,
    ):
        self.cache_ttl_s = cache_ttl_s
        self.cache_distance = cache_distance
        self.lock_stable_count = lock_stable_count
        self.ocr_request_interval_s = ocr_request_interval_s

        # track_id-bound state
        self.track_cache: Dict[int, Dict[str, Any]] = {}
        self.track_vote_history: Dict[int, List[Tuple[Dict[str, Any], float]]] = {}   # (read, time)
        self.track_last_request_time: Dict[int, float] = {}

        # Fallback bbox-bound state (used when track_id is not available)
        self.bbox_cache: Dict[Tuple[int, int, int, int], Tuple[str, float, float]] = {}
        self.bbox_vote_history: Dict[Tuple[int, int, int, int], List[Tuple[Dict[str, Any], float]]] = {}
        self.bbox_last_request_time: Dict[Tuple[int, int, int, int], float] = {}

    def reset(self):
        # Clear caches and vote history so a new video loop starts clean.
        self.track_cache.clear()
        self.track_vote_history.clear()
        self.track_last_request_time.clear()
        self.bbox_cache.clear()
        self.bbox_vote_history.clear()
        self.bbox_last_request_time.clear()

    def get_cached_ocr(
        self,
        bbox: Tuple[int, int, int, int],
        now: float,
        track_id: Optional[int] = None,
    ) -> Tuple[str, float]:
        # Retrieve cached OCR result by track_id if available, otherwise by bbox distance.
        # 1. Primary lookup: track_id
        if track_id is not None and track_id in self.track_cache:
            cached = self.track_cache[track_id]
            if now - cached.get("last_seen", 0.0) <= self.cache_ttl_s:
                cached_text = cached.get("final_text", "")
                cached_conf = cached.get("confidence", 0.0)
                if cached_text:
                    return cached_text, cached_conf

        # 2. Secondary fallback: bbox distance
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        best_text, best_conf = "", 0.0
        best_dist = self.cache_distance

        for k, v in list(self.bbox_cache.items()):
            if len(v) >= 3:
                text, conf, t_stamp = v[0], v[1], v[2]
            else:
                continue

            if now - t_stamp > self.cache_ttl_s:
                continue

            kx1, ky1, kx2, ky2 = k
            kcx, kcy = (kx1 + kx2) / 2.0, (ky1 + ky2) / 2.0
            dist = ((cx - kcx) ** 2 + (cy - kcy) ** 2) ** 0.5

            # Nearest entry wins, so two close plates don't borrow each other's text
            if dist < best_dist and text:
                best_text, best_conf, best_dist = text, conf, dist

        return best_text, best_conf

    def should_ocr(
        self,
        track_id: Optional[int],
        bbox: Tuple[int, int, int, int],
        now: float,
    ) -> bool:
        # Determine whether OCR is needed for this track or bbox.
        if track_id is not None:
            cached = self.track_cache.get(track_id)
            if cached is not None:
                # Already locked with stable/high confidence -> no more OCR needed
                if cached.get("is_locked", False):
                    return False
                last_req = self.track_last_request_time.get(track_id, 0.0)
                if now - last_req < self.ocr_request_interval_s:
                    return False
            else:
                last_req = self.track_last_request_time.get(track_id, 0.0)
                if now - last_req < self.ocr_request_interval_s:
                    return False
            return True

        # Fallback for unassigned bbox
        cached_text, _ = self.get_cached_ocr(bbox, now)
        if cached_text:
            return False
        last_req = self.bbox_last_request_time.get(bbox, 0.0)
        if now - last_req < self.ocr_request_interval_s:
            return False
        return True

    def record_ocr_request(
        self,
        track_id: Optional[int],
        bbox: Tuple[int, int, int, int],
        now: float,
    ):
        # Record the timestamp of an OCR request to enforce interval throttling.
        if track_id is not None:
            self.track_last_request_time[track_id] = now
        else:
            self.bbox_last_request_time[bbox] = now

    def update_cache(
        self,
        bbox: Tuple[int, int, int, int],
        final_text: str,
        final_conf: float,
        now: float,
        track_id: Optional[int] = None,
        plate_text: str = "",
        province_text: str = "",
        complete: bool = False,
    ):
        # Update the cache with a new voted OCR result; complete = both the series and the number are known.
        # Always maintain bbox fallback cache
        for k in [k for k, v in self.bbox_cache.items() if k != bbox and now - v[2] > self.cache_ttl_s]:
            del self.bbox_cache[k]
        _, old_bbox_conf, _ = self.bbox_cache.get(bbox, ("", 0.0, 0))
        if final_conf >= old_bbox_conf:
            self.bbox_cache[bbox] = (final_text, final_conf, now)

        if track_id is not None:
            # Clean expired track caches
            expired = [tid for tid, entry in self.track_cache.items()
                       if tid != track_id and now - entry.get("last_seen", 0.0) > self.cache_ttl_s]
            for tid in expired:
                del self.track_cache[tid]

            history = self.track_vote_history.get(track_id, [])
            vote_count = len(history)

            old_entry = self.track_cache.get(track_id)
            # Stable = the voted result came out identical on consecutive updates, not just "read N times"
            if old_entry is not None and old_entry.get("last_vote_text") == final_text:
                stable_count = old_entry.get("stable_count", 0) + 1
            else:
                stable_count = 1
            # A single read can be confidently wrong, so only lock once the vote is complete and stopped changing
            is_locked = complete and stable_count >= self.lock_stable_count

            # The voted result already weighs every read of the track, so it replaces the previous one
            self.track_cache[track_id] = {
                "plate_text": plate_text,
                "province_text": province_text,
                "final_text": final_text,
                "confidence": final_conf,
                "vote_count": vote_count,
                "is_locked": is_locked,
                "last_seen": now,
                "last_vote_text": final_text,
                "stable_count": stable_count,
            }

    def aggregate_vote_result(
        self,
        bbox: Tuple[int, int, int, int],
        read: Dict[str, Any],
        now: float,
        track_id: Optional[int] = None,
    ) -> Tuple[str, str, float, str, float]:
        # Votes the plate over recent reads of this track (or bbox), each weighted by score * sqrt(sharpness).
        # Returns (series, number, plate_conf, province, province_conf); a line with no valid vote is "".
        cutoff = now - self.cache_ttl_s
        histories, key = (self.track_vote_history, track_id) if track_id is not None else (self.bbox_vote_history, bbox)
        history = [entry for entry in histories.get(key, []) if entry[1] >= cutoff]
        history.append((read, now))
        histories[key] = history[-30:]
        for k in [k for k, h in histories.items() if k != key and h[-1][1] < cutoff]:
            del histories[k]

        def line_reads(line):
            return [(r[line][0], r[line][1], r[line][1] * math.sqrt(r["sharpness"])) for r, _ in history]

        series, series_conf = vote_line(line_reads("series"))
        number, number_conf = vote_line(line_reads("number"))
        series = series if is_valid_series(series) else ""
        number = number if is_valid_number(number) else ""
        plate_conf = min([conf for text, conf in ((series, series_conf), (number, number_conf)) if text], default=0.0)

        province = vote_temporal_strings([r["province"] for r, _ in history])
        province_conf = max((r["province"][1] for r, _ in history if r["province"][0] == province), default=0.0)
        return series, number, plate_conf, province, province_conf

    def prune_inactive_tracks(self, active_track_ids: set, now: float):
        # Prune track cache and vote history for tracks that are no longer active and have exceeded TTL.
        cutoff = now - self.cache_ttl_s
        for tid in list(self.track_cache.keys()):
            if tid not in active_track_ids:
                if self.track_cache[tid].get("last_seen", 0.0) < cutoff:
                    del self.track_cache[tid]
                    self.track_vote_history.pop(tid, None)
                    self.track_last_request_time.pop(tid, None)

    def is_locked(self, track_id: Optional[int]) -> bool:
        entry = self.track_cache.get(track_id) if track_id is not None else None
        return bool(entry and entry.get("is_locked", False))

    def vote_count(self, track_id: Optional[int]) -> int:
        entry = self.track_cache.get(track_id) if track_id is not None else None
        return entry.get("vote_count", 0) if entry else 0
