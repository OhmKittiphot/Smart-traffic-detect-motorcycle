import queue
import time

from province import extract_thai_province
from utils import format_thai_plate, log_error


class OCRResultHandler:
    """Processes OCR worker results, updates track voting caches, and logs plate detections."""

    def __init__(self, ocr_out_q, ocr_manager, tracker, det_lock, get_media_gen_fn, ocr_conf_threshold: float):
        self.ocr_out_q = ocr_out_q
        self.ocr_manager = ocr_manager
        self.tracker = tracker
        self.det_lock = det_lock
        self.get_media_gen_fn = get_media_gen_fn
        self.ocr_conf_threshold = ocr_conf_threshold
        self._running = True

    def stop(self):
        self._running = False

    def run_loop(self):
        while self._running:
            try:
                results = self.ocr_out_q.get(timeout=5.0)
            except queue.Empty:
                continue
            except (EOFError, OSError) as e:
                # The OCR process exited and the queue pipe is closed
                print(f"[OCR-Result] OCR queue closed ({e}). Stopping result handler.")
                return

            try:
                frame_t = time.time()
                result_gen = None
                if isinstance(results, dict):
                    if "error" in results:
                        print(f"[OCR-Result] OCR worker error: {results['error']}")
                        continue
                    result_gen = results.get("gen")
                    frame_t = float(results.get("t", frame_t))
                    results = results.get("results", [])
                if not isinstance(results, list):
                    continue
            except Exception as e:
                log_error("OCR-Result", e)
                continue

            # Handle each plate on its own so one malformed result does not drop the whole batch
            for item in results:
                try:
                    self._handle_item(item, result_gen, frame_t)
                except Exception as e:
                    log_error("OCR-Result", e)

    def _handle_item(self, item, result_gen, frame_t: float):
        track_id = None
        if len(item) >= 6:
            bbox, plate_text, plate_conf, province_text, province_conf, track_id = item[:6]
        elif len(item) == 5:
            bbox, plate_text, plate_conf, province_text, province_conf = item[:5]
        elif len(item) == 3:
            bbox, plate_text, plate_conf = item
            province_text, province_conf = "", 0.0
        else:
            bbox, plate_text = item[:2]
            plate_conf = 0.0
            province_text, province_conf = "", 0.0

        plate_text = str(plate_text or "").strip()
        province_text = str(province_text or "").strip()
        final_text = ""
        final_conf = 0.0

        with self.det_lock:
            if result_gen is not None and result_gen != self.get_media_gen_fn():
                return

            if track_id is None:
                track_id = self.tracker.resolve_track_id(bbox)

            plate_text, plate_conf, province_text, province_conf = self.ocr_manager.aggregate_vote_result(
                bbox=bbox,
                plate_text=plate_text,
                plate_conf=float(plate_conf) if plate_conf else 0.0,
                province_text=province_text,
                province_conf=float(province_conf) if province_conf else 0.0,
                now=frame_t,
                track_id=track_id,
            )

            if plate_text and province_text:
                final_text = f"{plate_text} {province_text}"
                final_conf = min(plate_conf, province_conf) if province_conf > 0 else plate_conf
            elif plate_text:
                final_text = plate_text
                final_conf = plate_conf
            else:
                final_text = province_text
                final_conf = province_conf

            if not (final_text and final_conf >= self.ocr_conf_threshold):
                return

            # The cache may keep an older, more confident read; take its plate/province too
            final_text, final_conf, plate_text, province_text = self.ocr_manager.update_cache(
                bbox=bbox,
                final_text=final_text,
                final_conf=final_conf,
                now=frame_t,
                track_id=track_id,
                plate_text=plate_text,
                province_text=province_text,
            )

            # Nothing is printed here: the plate goes into the motorcycle's report, printed when it leaves
            province = province_text or extract_thai_province(final_text)
            formatted_plate = format_thai_plate(plate_text, "") if plate_text else ""
            self.tracker.record_plate(track_id, formatted_plate, province, final_conf)
