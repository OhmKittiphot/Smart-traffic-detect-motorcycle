import time
from typing import Dict, List, Tuple, Any

from ocr_voting import vote_characters_by_position, vote_temporal_strings
from province import extract_thai_province


class OCRManager:
    """Manages OCR caching, history, and voting for license plates."""

    def __init__(self, cache_ttl_s: float, cache_distance: float):
        self.cache_ttl_s = cache_ttl_s
        self.cache_distance = cache_distance
        
        # Internal state
        self.ocr_cache: Dict[Tuple[int, int, int, int], Tuple[str, float, float]] = {}
        self.ocr_vote_history: Dict[Tuple[int, int, int, int], List[Tuple[str, str, float, float, float]]] = {}
        self.motorcycle_ocr_history: Dict[int, List[Tuple[str, float, int]]] = {}

    def get_cached_ocr(self, bbox: Tuple[int, int, int, int]) -> Tuple[str, float]:
        """Retrieve cached OCR result if available and within distance/TTL."""
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        now = time.time()
        best_text, best_conf = "", 0.0

        # Create a list from items to allow modification during iteration if needed later
        # (though we aren't modifying here, it matches original behavior)
        for k, v in list(self.ocr_cache.items()):
            if len(v) >= 3:
                text, conf, t_stamp = v[0], v[1], v[2]
            else:
                continue
                
            if now - t_stamp > self.cache_ttl_s:
                continue
                
            kx1, ky1, kx2, ky2 = k
            kcx, kcy = (kx1 + kx2) / 2.0, (ky1 + ky2) / 2.0
            dist = ((cx - kcx) ** 2 + (cy - kcy) ** 2) ** 0.5
            
            if dist < self.cache_distance and text:
                best_text, best_conf = text, conf
                break
                
        return best_text, best_conf

    def update_cache(self, bbox: Tuple[int, int, int, int], final_text: str, final_conf: float) -> Tuple[str, float]:
        """Update the cache with a new OCR result if confidence is higher. Returns the final accepted (text, conf)."""
        now = time.time()
        old_text, old_conf, _ = self.ocr_cache.get(bbox, ("", 0.0, 0))
        if final_conf >= old_conf:
            self.ocr_cache[bbox] = (final_text, final_conf, now)
            return final_text, final_conf
        return old_text, old_conf

    def aggregate_vote_result(self, bbox: Tuple[int, int, int, int], plate_text: str, plate_conf: float, province_text: str, province_conf: float) -> Tuple[str, float, str, float]:
        """Aggregate OCR results over time using voting."""
        now = time.time()
        history = self.ocr_vote_history.setdefault(bbox, [])
        history.append((plate_text, province_text, plate_conf, province_conf, now))

        # Remove old history outside of TTL
        cutoff = now - self.cache_ttl_s
        history = [entry for entry in history if entry[4] >= cutoff and (entry[0] or entry[1])]
        self.ocr_vote_history[bbox] = history

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

    def add_motorcycle_ocr(self, track_id: int, final_text: str, final_conf: float) -> None:
        """Add OCR result to a specific motorcycle track."""
        history = self.motorcycle_ocr_history.setdefault(track_id, [])
        history.append((final_text, final_conf, int(time.time() * 1000)))
        if len(history) > 30:
            history = history[-30:]
        self.motorcycle_ocr_history[track_id] = history

    def get_motorcycle_consensus(self, track_id: int) -> Dict[str, Any]:
        """Get the consensus OCR result for a motorcycle track."""
        history = self.motorcycle_ocr_history.get(track_id, [])
        if not history:
            return {"plate_text": "", "province_text": "", "consensus_conf": 0.0, "ocr_count": 0, "history_len": 0}

        plate_candidates = [(text, conf) for text, conf, _ in history if text]
        plate_text = vote_characters_by_position(plate_candidates) if plate_candidates else ""
        plate_conf = max((conf for _, conf in plate_candidates), default=0.0)

        province_text = ""
        province_conf = 0.0
        if plate_candidates:
            for text, _ in plate_candidates:
                province = extract_thai_province(text)
                if province:
                    province_text = province
                    province_conf = plate_conf
                    break

        if not plate_text and plate_candidates:
            plate_text = plate_candidates[-1][0]

        return {
            "plate_text": plate_text,
            "province_text": province_text,
            "consensus_conf": plate_conf,
            "ocr_count": len(history),
            "province_conf": province_conf,
            "history_len": len(history),
            "latest_timestamp": max((entry[2] for entry in history), default=0)
        }
