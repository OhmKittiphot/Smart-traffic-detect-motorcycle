import queue

import cv2
import torch

from app_config import load_yolo_model
from report import print_motorcycle_reports
from utils import log_error, merge_related_boxes


class YoloPipeline:
    """Handles YOLO model inference for license plates, motorcycles, and helmet detection."""

    def __init__(self, config, device, use_half, tracker, speed_estimator, ocr_manager, ocr_in_q, det_lock):
        self.config = config
        self.device = device
        self.use_half = use_half
        self.tracker = tracker
        self.speed_estimator = speed_estimator
        self.ocr_manager = ocr_manager
        self.ocr_in_q = ocr_in_q
        self.det_lock = det_lock

        print("Loading YOLO models...")
        self.model = load_yolo_model(config.model_path, device, use_half)
        try:
            self.model_mc = load_yolo_model(config.motorcycle_model_path, device, use_half)
        except Exception as e:
            print(f"[Motorcycle] Model not loaded ({e}). Motorcycle detection disabled.")
            self.model_mc = None

        try:
            self.model_helmet = load_yolo_model(config.helmet_model_path, device, use_half)
        except Exception as e:
            print(f"[Helmet] Model not loaded ({e}). Helmet detection disabled.")
            self.model_helmet = None

        self.helmet_ids = set()
        self.head_ids = set()
        if self.model_helmet is not None:
            norm = lambda s: str(s).lower().replace("_", " ").replace("-", " ").strip()
            helmet_names = {norm(n) for n in config.helmet_class_names}
            head_names = {norm(n) for n in config.no_helmet_class_names}
            for cid, name in self.model_helmet.names.items():
                if norm(name) in helmet_names:
                    self.helmet_ids.add(int(cid))
                elif norm(name) in head_names:
                    self.head_ids.add(int(cid))
            print(f"[Helmet] Model classes: {dict(self.model_helmet.names)}")
            if not self.head_ids:
                print("[Helmet] No bare-head/no-helmet class found; NO HELMET will never be reported.")

    @staticmethod
    def crop_rider_rois(frame, mc_bboxes):
        """Crop rider/head Region of Interest (ROI) for each detected motorcycle."""
        h, w = frame.shape[:2]
        rois = []
        crops = []

        for (mx1, my1, mx2, my2), _ in mc_bboxes:
            mx1, mx2 = min(mx1, mx2), max(mx1, mx2)
            my1, my2 = min(my1, my2), max(my1, my2)
            mw = mx2 - mx1
            mh = my2 - my1

            # Expand horizontally (+-15%) and vertically upward (-85% mh) to capture rider head/helmet
            rx1 = max(0, int(mx1 - 0.15 * mw))
            ry1 = max(0, int(my1 - 0.85 * mh))
            rx2 = min(w, int(mx2 + 0.15 * mw))
            ry2 = min(h, int(my1 + 0.65 * mh))

            if (rx2 - rx1) < 20 or (ry2 - ry1) < 20:
                continue

            crop = frame[ry1:ry2, rx1:rx2]
            if crop.size == 0:
                continue

            crops.append(crop)
            rois.append((rx1, ry1, rx2, ry2))

        return crops, rois

    @staticmethod
    def suppress_overlapping_boxes(boxes, scores, iou_thresh=0.45):
        """Deduplicate overlapping bounding boxes across multiple crops using NMS."""
        if not boxes:
            return []
        cv_boxes = [[x1, y1, x2 - x1, y2 - y1] for x1, y1, x2, y2 in boxes]
        indices = cv2.dnn.NMSBoxes(cv_boxes, scores, score_threshold=0.0, nms_threshold=iou_thresh)
        if len(indices) == 0:
            return []
        return [boxes[int(i)] for i in indices]

    def process_frame(self, frame, frame_t: float, frame_gen: int, current_media_gen: int):
        """Runs the detection pipeline for a single frame."""
        with torch.inference_mode():
            results = self.model.predict(
                frame,
                imgsz=self.config.plate_imgsz,
                conf=self.config.detection_conf,
                verbose=False,
                device=self.device,
                quantize=self.config.inference_quantize,
            )

            mc_bboxes = []
            if self.model_mc is not None:
                res_mc = self.model_mc.predict(
                    frame,
                    imgsz=self.config.detection_imgsz,
                    classes=[3],
                    conf=self.config.detection_conf,
                    verbose=False,
                    device=self.device,
                    quantize=self.config.inference_quantize,
                )
                for r_mc in res_mc:
                    for box_mc in r_mc.boxes:
                        mx1, my1, mx2, my2 = map(int, box_mc.xyxy[0].tolist())
                        m_conf = float(box_mc.conf[0])
                        mc_bboxes.append(((mx1, my1, mx2, my2), m_conf))

            helmet_bboxes = []
            head_bboxes = []
            if self.model_helmet is not None and mc_bboxes:
                crops, rois = self.crop_rider_rois(frame, mc_bboxes)
                if crops:
                    res_h = self.model_helmet.predict(
                        crops,
                        imgsz=self.config.helmet_imgsz,
                        conf=self.config.helmet_conf,
                        verbose=False,
                        device=self.device,
                        quantize=self.config.inference_quantize,
                    )
                    raw_helmets, raw_h_scores = [], []
                    raw_heads, raw_hd_scores = [], []

                    for r_h, (rx1, ry1, _, _) in zip(res_h, rois):
                        for box_h in r_h.boxes:
                            bx1, by1, bx2, by2 = map(int, box_h.xyxy[0].tolist())
                            score = float(box_h.conf[0])
                            cls_h = int(box_h.cls[0])
                            orig_box = (rx1 + bx1, ry1 + by1, rx1 + bx2, ry1 + by2)

                            if cls_h in self.head_ids:
                                raw_heads.append(orig_box)
                                raw_hd_scores.append(score)
                            elif cls_h in self.helmet_ids:
                                raw_helmets.append(orig_box)
                                raw_h_scores.append(score)

                    helmet_bboxes = self.suppress_overlapping_boxes(raw_helmets, raw_h_scores)
                    head_bboxes = self.suppress_overlapping_boxes(raw_heads, raw_hd_scores)

        with self.det_lock:
            if frame_gen < current_media_gen:
                return []
            finished = self.tracker.update_tracks(mc_bboxes, frame_t)
            if self.model_helmet is not None:
                self.tracker.update_helmets(helmet_bboxes, head_bboxes)
            if self.speed_estimator.calibrated:
                for mc_bb, tid in self.tracker.bbox_to_track_id.items():
                    self.tracker.record_speed(tid, self.speed_estimator.update(tid, mc_bb, frame_t))
                self.speed_estimator.prune(frame_t)

        # Motorcycles that have left the scene: print their summary outside the lock
        print_motorcycle_reports(finished, self.speed_estimator.calibrated,
                                 self.config.report_min_frames, self.config.report_min_conf)

        all_boxes = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                conf_yolo = float(box.conf[0])
                h, w = frame.shape[:2]
                if (x2 - x1) < w * 0.4 and (y2 - y1) < h * 0.4:
                    all_boxes.append([x1, y1, x2, y2, conf_yolo])

        bboxes_info = merge_related_boxes(all_boxes)

        bboxes = []
        for (x1, y1, x2, y2, conf_yolo) in bboxes_info:
            h, w = frame.shape[:2]
            bw, bh = x2 - x1, y2 - y1
            pad_w = int(bw * 0.10)
            pad_h = int(bh * 0.10)
            x1_p, y1_p = max(0, x1 - pad_w), max(0, y1 - pad_h)
            x2_p, y2_p = min(w, x2 + pad_w), min(h, y2 + pad_h)

            crop = frame[y1_p:y2_p, x1_p:x2_p]
            if crop.size > 0:
                bboxes.append(((x1, y1, x2, y2), crop))

        current_dets = []
        if bboxes:
            ocr_needed = []
            with self.det_lock:
                for (bb, cr) in bboxes:
                    track_id = self.tracker.resolve_track_id(bb)
                    cached_text, cached_conf = self.ocr_manager.get_cached_ocr(bb, frame_t, track_id=track_id)
                    current_dets.append((bb, cached_text, cached_conf))
                    if self.ocr_manager.should_ocr(track_id, bb, frame_t):
                        self.ocr_manager.record_ocr_request(track_id, bb, frame_t)
                        ocr_needed.append((bb, cr, track_id))

                self.ocr_manager.prune_inactive_tracks(set(self.tracker.motorcycle_tracks.keys()), frame_t)

            if ocr_needed:
                payload = {
                    "gen": frame_gen,
                    "t": frame_t,
                    "items": [
                        (bb, cr.tobytes(), cr.shape, str(cr.dtype), tid)
                        for bb, cr, tid in ocr_needed
                    ],
                }
                try:
                    self.ocr_in_q.put_nowait(payload)
                except queue.Full:
                    pass  # OCR is busy; these plates will be requested again on a later frame
                except Exception as e:
                    log_error("YOLO->OCR", e)
        else:
            with self.det_lock:
                self.ocr_manager.prune_inactive_tracks(set(self.tracker.motorcycle_tracks.keys()), frame_t)

        return current_dets
