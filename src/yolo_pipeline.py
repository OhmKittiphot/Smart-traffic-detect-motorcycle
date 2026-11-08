import queue

import numpy as np
import torch

from .detectors import HeadDetector, MotorcycleDetector, PlateDetector, RiderClassification
from .ocr import plate_sharpness
from .report import print_motorcycle_reports
from .utils import log, log_error, merge_related_boxes


class YoloPipeline:
    # What happens to each frame: detect motorcycles, plates and riders (detectors.py), track the
    # motorcycles, count riders, measure speeds, report the motorcycles that left, and send new plates to OCR.

    def __init__(self, config, device, use_half, tracker, speed_estimator, ocr_manager, ocr_in_q, det_lock,
                 zone):
        self.config = config
        self.tracker = tracker
        self.zone = zone   # DetectionZone: motorcycles outside it are dropped before tracking
        self.speed_estimator = speed_estimator
        self.ocr_manager = ocr_manager
        self.ocr_in_q = ocr_in_q
        self.det_lock = det_lock

        log.info("Loading YOLO models...")
        self.plates = PlateDetector(config, device, use_half)
        self.motorcycles = MotorcycleDetector(config, device, use_half)
        # Both run: the rider classifier counts the riders on each whole motorcycle, the head detector and
        # helmet classifier decide helmet or not on each head up close (RiderCounter combines the two)
        self.rider_classification = RiderClassification(config, device, use_half)
        self.heads = HeadDetector(config, device, use_half)

    def warmup(self, frame_shape=(1080, 1920, 3)):
        # Run every model once on a blank frame so CUDA kernels load before playback starts
        # (the first real inference otherwise takes several seconds).
        frame = np.zeros(frame_shape, dtype=np.uint8)
        for model in (self.plates, self.motorcycles, self.heads, self.rider_classification):
            model.warmup(frame)

    def reset(self):
        # Drop per-track rider votes (track IDs restart with the media session).
        self.rider_classification.reset()

    def process_frame(self, frame, frame_t: float, frame_gen: int, current_media_gen: int):
        # Runs the detection pipeline for a single frame; returns the plates found with their cached OCR
        # text, [(box, text, conf)].
        with torch.inference_mode():
            mc_bboxes = self.zone.keep(self.motorcycles.detect(frame))
            # Needs the motorcycles first: in "roi" mode plates are only searched inside them
            search_rois = self.config.plate_search == "roi" and self.motorcycles.available
            plate_boxes = self.plates.detect(frame, mc_bboxes, search_rois)
            heads = self.heads.detect(frame, mc_bboxes) if self.heads.available and mc_bboxes else None

        with self.det_lock:
            if frame_gen < current_media_gen:
                return []   # a seek or source switch happened meanwhile
            finished = self.tracker.update_tracks(mc_bboxes, frame_t)
            for mc_bb, tid in self.tracker.bbox_to_track_id.items():
                self.tracker.record_snapshot(tid, frame, mc_bb)
            if self.heads.available:
                self.tracker.riders.update_heads(self.tracker.bbox_to_track_id, *(heads or ([], [], [])))
            mc_ids = dict(self.tracker.bbox_to_track_id)

        # Needs the track IDs, so it runs after tracking (outside the lock: the classifier is the slow part)
        if self.rider_classification.available and mc_ids:
            self._classify_riders(frame, mc_ids)
        self._update_speeds(frame_t)
        # Motorcycles that have left the scene: print their summary outside the lock
        print_motorcycle_reports(finished, self.speed_estimator.calibrated, self.config.report_min_frames)
        return self._read_plates(frame, frame_t, frame_gen, plate_boxes)

    def _classify_riders(self, frame, mc_ids):
        summaries = self.rider_classification.classify(frame, mc_ids, self.det_lock)
        with self.det_lock:
            for tid, summary in summaries.items():
                if tid in self.tracker.motorcycle_tracks:
                    self.tracker.riders.update_summary(tid, summary)

    def _update_speeds(self, frame_t: float):
        with self.det_lock:
            if self.speed_estimator.calibrated:
                for mc_bb, tid in self.tracker.bbox_to_track_id.items():
                    self.tracker.record_speed(tid, self.speed_estimator.update(tid, mc_bb, frame_t))
                self.speed_estimator.prune(frame_t)

    @staticmethod
    def _plate_crops(frame, plate_boxes) -> list:
        # [(box, crop)] of each plate, nearby fragments merged and with 10% padding around the crop.
        h, w = frame.shape[:2]
        boxes = [b for b in plate_boxes if (b[2] - b[0]) < w * 0.4 and (b[3] - b[1]) < h * 0.4]
        crops = []
        for x1, y1, x2, y2, _ in merge_related_boxes(boxes):
            pad_w, pad_h = int((x2 - x1) * 0.10), int((y2 - y1) * 0.10)
            crop = frame[max(0, y1 - pad_h):min(h, y2 + pad_h), max(0, x1 - pad_w):min(w, x2 + pad_w)]
            if crop.size > 0:
                crops.append(((x1, y1, x2, y2), crop))
        return crops

    def _read_plates(self, frame, frame_t: float, frame_gen: int, plate_boxes) -> list:
        # Attach each plate to its motorcycle, keep its photo, and send the plates that need a (new) read
        # to the OCR process. Returns [(box, cached text, cached conf)] for drawing.
        current_dets, ocr_needed = [], []
        with self.det_lock:
            for bb, crop in self._plate_crops(frame, plate_boxes):
                track_id = self.tracker.resolve_track_id(bb)
                # Evidence photo of the plate, also when OCR cannot read it
                self.tracker.record_plate_snapshot(track_id, crop, plate_sharpness(crop))
                cached_text, cached_conf = self.ocr_manager.get_cached_ocr(bb, frame_t, track_id=track_id)
                current_dets.append((bb, cached_text, cached_conf))
                if self.ocr_manager.should_ocr(track_id, bb, frame_t):
                    self.ocr_manager.record_ocr_request(track_id, bb, frame_t)
                    ocr_needed.append((bb, crop, track_id))
            self.ocr_manager.prune_inactive_tracks(set(self.tracker.motorcycle_tracks.keys()), frame_t)

        if ocr_needed:
            payload = {
                "gen": frame_gen,
                "t": frame_t,
                "items": [(bb, crop.tobytes(), crop.shape, str(crop.dtype), tid) for bb, crop, tid in ocr_needed],
            }
            try:
                self.ocr_in_q.put_nowait(payload)
            except queue.Full:
                pass  # OCR is busy; these plates will be requested again on a later frame
            except Exception as e:
                log_error("YOLO->OCR", e)
        return current_dets
