import math
from typing import Any, Dict, List, Optional, Tuple

from .ocr import is_valid_number, is_valid_series
from .ocr_voting import vote_line, vote_temporal_strings
from .utils import bbox_distance

Box = Tuple[int, int, int, int]


class OCRManager:
    # OCR requests, voting and the remembered result of each licence plate, by track ID (by box for a plate
    # without a track).

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

        # By track ID. A cache entry: final_text, confidence, last_seen, stable_count, is_locked
        self.track_cache: Dict[int, Dict[str, Any]] = {}
        self.track_vote_history: Dict[int, List[Tuple[Dict[str, Any], float]]] = {}   # (read, time)
        self.track_last_request_time: Dict[int, float] = {}

        # By box, for plates without a track. A cache entry: (text, confidence, time)
        self.bbox_cache: Dict[Box, Tuple[str, float, float]] = {}
        self.bbox_vote_history: Dict[Box, List[Tuple[Dict[str, Any], float]]] = {}
        self.bbox_last_request_time: Dict[Box, float] = {}

    def reset(self):
        # Clear caches and vote history so a new video loop starts clean.
        self.track_cache.clear()
        self.track_vote_history.clear()
        self.track_last_request_time.clear()
        self.bbox_cache.clear()
        self.bbox_vote_history.clear()
        self.bbox_last_request_time.clear()

    def get_cached_ocr(self, bbox: Box, now: float, track_id: Optional[int] = None) -> Tuple[str, float]:
        # (text, confidence) remembered for the track, else for the nearest box read within cache_distance;
        # ("", 0.0) when there is none.
        cached = self.track_cache.get(track_id) if track_id is not None else None
        if cached is not None and now - cached["last_seen"] <= self.cache_ttl_s:
            return cached["final_text"], cached["confidence"]

        best_text, best_conf, best_dist = "", 0.0, self.cache_distance
        for box, (text, conf, seen_at) in self.bbox_cache.items():
            if now - seen_at > self.cache_ttl_s:
                continue
            # Nearest entry wins, so two close plates don't borrow each other's text
            dist = bbox_distance(bbox, box)
            if dist < best_dist:
                best_text, best_conf, best_dist = text, conf, dist
        return best_text, best_conf

    def should_ocr(self, track_id: Optional[int], bbox: Box, now: float) -> bool:
        # Whether to send this plate to OCR: not once its track's plate is locked, nor for a box without a
        # track that already has a remembered read, nor within ocr_request_interval_s of the last request.
        if track_id is not None:
            cached = self.track_cache.get(track_id)
            if cached is not None and cached["is_locked"]:
                return False
            last_request = self.track_last_request_time.get(track_id, 0.0)
        else:
            if self.get_cached_ocr(bbox, now)[0]:
                return False
            last_request = self.bbox_last_request_time.get(bbox, 0.0)
        return now - last_request >= self.ocr_request_interval_s

    def record_ocr_request(self, track_id: Optional[int], bbox: Box, now: float):
        # Record the timestamp of an OCR request to enforce interval throttling.
        if track_id is not None:
            self.track_last_request_time[track_id] = now
        else:
            self.bbox_last_request_time[bbox] = now

    def update_cache(self, bbox: Box, final_text: str, final_conf: float, now: float,
                     track_id: Optional[int] = None, complete: bool = False):
        # Remember a new voted result (final_text is never empty); complete = both the series and the number
        # are known.
        for box in [b for b, entry in self.bbox_cache.items() if b != bbox and now - entry[2] > self.cache_ttl_s]:
            del self.bbox_cache[box]
        if final_conf >= self.bbox_cache.get(bbox, ("", 0.0, 0.0))[1]:
            self.bbox_cache[bbox] = (final_text, final_conf, now)

        if track_id is None:
            return
        for tid in [t for t, entry in self.track_cache.items()
                    if t != track_id and now - entry["last_seen"] > self.cache_ttl_s]:
            del self.track_cache[tid]
        old = self.track_cache.get(track_id)
        # Stable = the voted result came out identical on consecutive updates, not just "read N times"
        stable_count = old["stable_count"] + 1 if old is not None and old["final_text"] == final_text else 1
        # The voted result already weighs every read of the track, so it replaces the previous one. A single
        # read can be confidently wrong, so the plate is only locked once the vote is complete and stopped changing
        self.track_cache[track_id] = {
            "final_text": final_text,
            "confidence": final_conf,
            "last_seen": now,
            "stable_count": stable_count,
            "is_locked": complete and stable_count >= self.lock_stable_count,
        }

    def aggregate_vote_result(
        self,
        bbox: Box,
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
        # Forget the tracks no longer in view whose plate was last read more than cache_ttl_s ago.
        cutoff = now - self.cache_ttl_s
        for tid in [t for t, entry in self.track_cache.items()
                    if t not in active_track_ids and entry["last_seen"] < cutoff]:
            del self.track_cache[tid]
            self.track_vote_history.pop(tid, None)
            self.track_last_request_time.pop(tid, None)
