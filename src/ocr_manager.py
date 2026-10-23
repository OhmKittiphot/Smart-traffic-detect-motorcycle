from typing import Dict, List, Tuple, Any, Optional

from ocr_voting import vote_characters_by_position, vote_temporal_strings


class OCRManager:
    """Manages OCR caching, history, and voting for license plates, bound to track_id."""

    def __init__(
        self,
        cache_ttl_s: float = 5.0,
        cache_distance: float = 120.0,
        lock_confidence: float = 0.90,
        lock_stable_count: int = 3,
        ocr_request_interval_s: float = 0.3,
    ):
        self.cache_ttl_s = cache_ttl_s
        self.cache_distance = cache_distance
        self.lock_confidence = lock_confidence
        self.lock_stable_count = lock_stable_count
        self.ocr_request_interval_s = ocr_request_interval_s

        # track_id-bound state
        self.track_cache: Dict[int, Dict[str, Any]] = {}
        self.track_vote_history: Dict[int, List[Tuple[str, str, float, float, float]]] = {}
        self.track_last_request_time: Dict[int, float] = {}

        # Fallback bbox-bound state (used when track_id is not available)
        self.bbox_cache: Dict[Tuple[int, int, int, int], Tuple[str, float, float]] = {}
        self.bbox_vote_history: Dict[Tuple[int, int, int, int], List[Tuple[str, str, float, float, float]]] = {}
        self.bbox_last_request_time: Dict[Tuple[int, int, int, int], float] = {}

    def reset(self) -> None:
        """Clear caches and vote history so a new video loop starts clean."""
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
        """Retrieve cached OCR result by track_id if available, otherwise by bbox distance."""
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
        """Determine whether OCR is needed for this track or bbox."""
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
    ) -> None:
        """Record the timestamp of an OCR request to enforce interval throttling."""
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
    ) -> Tuple[str, float, str, str]:
        """Update the cache with a new OCR result.
        Returns the accepted (final_text, conf, plate_text, province_text), all from the same read."""
        # Always maintain bbox fallback cache
        for k in [k for k, v in self.bbox_cache.items() if k != bbox and now - v[2] > self.cache_ttl_s]:
            del self.bbox_cache[k]
        old_bbox_text, old_bbox_conf, _ = self.bbox_cache.get(bbox, ("", 0.0, 0))
        if final_conf >= old_bbox_conf:
            self.bbox_cache[bbox] = (final_text, final_conf, now)

        if track_id is not None:
            # Clean expired track caches
            for tid in [tid for tid, entry in self.track_cache.items() if tid != track_id and now - entry.get("last_seen", 0.0) > self.cache_ttl_s]:
                del self.track_cache[tid]

            history = self.track_vote_history.get(track_id, [])
            vote_count = len(history)

            old_entry = self.track_cache.get(track_id)
            # Stable = the voted result came out identical on consecutive updates, not just "read N times"
            if old_entry is not None and old_entry.get("last_vote_text") == final_text:
                stable_count = old_entry.get("stable_count", 0) + 1
            else:
                stable_count = 1
            is_locked = (final_conf >= self.lock_confidence) or (stable_count >= self.lock_stable_count)

            if old_entry is None or final_conf >= old_entry.get("confidence", 0.0):
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
                return final_text, final_conf, plate_text, province_text
            else:
                old_entry["last_seen"] = now
                old_entry["vote_count"] = vote_count
                old_entry["last_vote_text"] = final_text
                old_entry["stable_count"] = stable_count
                old_entry["is_locked"] = old_entry.get("is_locked", False) or is_locked
                return (old_entry["final_text"], old_entry["confidence"],
                        old_entry.get("plate_text", ""), old_entry.get("province_text", ""))

        return final_text, final_conf, plate_text, province_text

    def aggregate_vote_result(
        self,
        bbox: Tuple[int, int, int, int],
        plate_text: str,
        plate_conf: float,
        province_text: str,
        province_conf: float,
        now: float,
        track_id: Optional[int] = None,
    ) -> Tuple[str, float, str, float]:
        """Aggregate OCR results over time using voting, bound to track_id (or bbox fallback)."""
        cutoff = now - self.cache_ttl_s

        if track_id is not None:
            history = self.track_vote_history.setdefault(track_id, [])
            history.append((plate_text, province_text, plate_conf, province_conf, now))
            history = [entry for entry in history if entry[4] >= cutoff and (entry[0] or entry[1])]
            if len(history) > 30:
                history = history[-30:]
            self.track_vote_history[track_id] = history

            for k in [k for k, h in self.track_vote_history.items() if k != track_id and (not h or h[-1][4] < cutoff)]:
                del self.track_vote_history[k]
        else:
            history = self.bbox_vote_history.setdefault(bbox, [])
            history.append((plate_text, province_text, plate_conf, province_conf, now))
            history = [entry for entry in history if entry[4] >= cutoff and (entry[0] or entry[1])]
            self.bbox_vote_history[bbox] = history

            for k in [k for k, h in self.bbox_vote_history.items() if k != bbox and (not h or h[-1][4] < cutoff)]:
                del self.bbox_vote_history[k]

        plate_candidates = [(text, conf) for text, _, conf, _, _ in history if text]
        province_candidates = [(text, conf) for _, text, _, conf, _ in history if text]

        voted_plate = vote_characters_by_position(plate_candidates) if plate_candidates else ""
        voted_province = vote_temporal_strings(province_candidates) if province_candidates else ""

        if not voted_plate and not voted_province:
            return "", 0.0, "", 0.0

        plate_out = voted_plate if voted_plate else plate_text
        plate_out_conf = max((conf for _, conf in plate_candidates), default=plate_conf)

        province_out = voted_province if voted_province else province_text
        province_out_conf = max((conf for _, conf in province_candidates), default=province_conf)

        return plate_out, plate_out_conf, province_out, province_out_conf

    def prune_inactive_tracks(self, active_track_ids: set, now: float) -> None:
        """Prune track cache and vote history for tracks that are no longer active and have exceeded TTL."""
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
