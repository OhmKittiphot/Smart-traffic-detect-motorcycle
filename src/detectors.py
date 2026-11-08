# The models run on each frame, one class per model: motorcycles, licence plates, rider heads (head
# detector, with the helmet classifier deciding helmet or not) and the rider classifier. Optional models
# that are missing are skipped (available = False).
import os

import torch

from .app_config import load_yolo_model
from .cuda_graphs import split_batch
from .helmet_classifier import HelmetClassifier, crop_head
from .rider_classifier import RiderClassifier
from .rider_voting import RiderVoting
from .utils import log, log_error, suppress_overlapping_boxes


def _boxes(result):
    # (x1, y1, x2, y2, conf, class) of each box of one YOLO result, as plain numbers.
    for b in result.boxes:
        yield (*map(int, b.xyxy[0].tolist()), float(b.conf[0]), int(b.cls[0]))


class _YoloModel:
    # One YOLO model and the predict() options every call shares.

    def __init__(self, config, device):
        self.config = config
        self.device = device
        self.model = None

    @property
    def available(self) -> bool:
        return self.model is not None

    def _predict(self, images, imgsz: int, **options):
        return self.model.predict(images, imgsz=imgsz, verbose=False, device=self.device,
                                  quantize=self.config.inference_quantize, **options)

    def _predict_crops(self, crops: list, imgsz: int, **options) -> list:
        # predict() on image crops, one result each. Every crop is letterboxed to the same imgsz square
        # (rect=False), and on a GPU the crops go in padded batches, so a few CUDA graphs cover any number of
        # them (cuda_graphs.split_batch).
        if not str(self.device).startswith("cuda"):
            return self._predict(crops, imgsz, rect=False, **options)
        results = []
        for part, size in split_batch(crops):
            results += self._predict(part + part[:1] * (size - len(part)), imgsz, rect=False, **options)[:len(part)]
        return results

    def _warmup(self, image, imgsz: int):
        # Run once on a blank image so CUDA kernels load before playback starts.
        if self.model is None:
            return
        try:
            with torch.inference_mode():
                self._predict(image, imgsz)
        except Exception as e:
            log_error("Warmup", e)


class MotorcycleDetector(_YoloModel):
    def __init__(self, config, device, use_half):
        super().__init__(config, device)
        self.classes = None
        path = config.motorcycle_model_path
        if os.path.isfile(path):
            try:
                self.model = load_yolo_model(path, device, use_half)
                # Works for both the custom detector ({0: motorcycle}) and COCO models ({3: motorcycle})
                self.classes = [i for i, n in self.model.names.items() if n in ("motorcycle", "motorbike")] or None
                log.info("[Motorcycle] Detector loaded: %s", path)
            except Exception as e:
                self.model = None
                log.warning("[Motorcycle] Model '%s' not loaded (%s).", path, e)
        if self.model is None:
            log.warning("[Motorcycle] No detector found. Motorcycle detection disabled.")

    def warmup(self, frame):
        self._warmup(frame, self.config.detection_imgsz)

    def detect(self, frame) -> list:
        # [((x1, y1, x2, y2), conf)] of each motorcycle (empty without the model).
        if self.model is None:
            return []
        results = self._predict(frame, self.config.detection_imgsz, classes=self.classes,
                                conf=self.config.detection_conf)
        return [((x1, y1, x2, y2), conf) for r in results for x1, y1, x2, y2, conf, _ in _boxes(r)]


class PlateDetector(_YoloModel):
    def __init__(self, config, device, use_half):
        super().__init__(config, device)
        self.model = load_yolo_model(config.plate_model_path, device, use_half)
        # Class IDs that are whole plates. The Thai plate model also has 'letters' and 'province' classes;
        # those parts would otherwise be sent to OCR as plates of their own (None = every class)
        self.classes = [i for i, n in self.model.names.items()
                        if n in ("license-plate", "license_plate", "plate")] or None

    def warmup(self, frame):
        roi = self.config.plate_search == "roi"
        self._warmup(frame, self.config.plate_roi_imgsz if roi else self.config.plate_imgsz)

    def _crop_rois(self, frame, mc_bboxes):
        # Regions where each motorcycle's plate can be: the bike box, widened by plate_roi_pad because
        # in side and rear-quarter views the plate on the rear fender sits at or just past the box edge.
        # Each region is returned as (x1, y1, bike_height).
        h, w = frame.shape[:2]
        pad_x, pad_y = self.config.plate_roi_pad
        crops, rois = [], []
        for (mx1, my1, mx2, my2), _ in mc_bboxes:
            mw, mh = mx2 - mx1, my2 - my1
            if mh < self.config.plate_min_bike_h:
                continue
            rx1 = max(0, int(mx1 - pad_x * mw))
            ry1 = max(0, int(my1 - pad_y * mh))
            rx2 = min(w, int(mx2 + pad_x * mw))
            ry2 = min(h, int(my2 + pad_y * mh))
            crop = frame[ry1:ry2, rx1:rx2]
            if crop.size:
                crops.append(crop)
                rois.append((rx1, ry1, mh))
        return crops, rois

    def detect(self, frame, mc_bboxes, search_rois: bool) -> list:
        # Plate boxes [x1, y1, x2, y2, conf] in frame coordinates. search_rois: look only inside the
        # motorcycles (config.plate_search "roi"), else in the whole frame.
        options = dict(conf=self.config.plate_conf, classes=self.classes)
        if not search_rois:
            results = self._predict(frame, self.config.plate_imgsz, **options)
            return [[x1, y1, x2, y2, conf] for r in results for x1, y1, x2, y2, conf, _ in _boxes(r)]

        crops, rois = self._crop_rois(frame, mc_bboxes)
        if not crops:
            return []   # no motorcycle close enough: no plate inference at all
        boxes, scores = [], []
        for r, (rx1, ry1, bike_h) in zip(self._predict_crops(crops, self.config.plate_roi_imgsz, **options), rois):
            for bx1, by1, bx2, by2, conf, _ in _boxes(r):
                # The model sometimes boxes a whole small bike, a wheel or the rider as a plate
                if by2 - by1 > self.config.plate_max_bike_h_ratio * bike_h:
                    continue
                boxes.append((rx1 + bx1, ry1 + by1, rx1 + bx2, ry1 + by2))
                scores.append(conf)
        # Regions of neighbouring bikes overlap, so one plate can be found twice
        keep = suppress_overlapping_boxes(list(range(len(boxes))), scores, boxes_xyxy=boxes)
        return [[*boxes[i], scores[i]] for i in keep]


class HeadDetector(_YoloModel):
    # Rider heads above each motorcycle, with and without a helmet. With the helmet classifier loaded, the
    # head detector only finds the heads and the classifier decides helmet or not on each head crop.

    def __init__(self, config, device, use_half):
        super().__init__(config, device)
        self.helmet_ids, self.head_ids = set(), set()
        self.classifier = None
        try:
            self.model = load_yolo_model(config.head_model_path, device, use_half)
        except Exception as e:
            log.warning("[Helmet] Model not loaded (%s). Helmet detection disabled.", e)
            return
        norm = lambda s: str(s).lower().replace("_", " ").replace("-", " ").strip()
        helmet_names = {norm(n) for n in config.helmet_class_names}
        head_names = {norm(n) for n in config.no_helmet_class_names}
        for cid, name in self.model.names.items():
            if norm(name) in helmet_names:
                self.helmet_ids.add(int(cid))
            elif norm(name) in head_names:
                self.head_ids.add(int(cid))
        log.info("[Helmet] Model classes: %s", dict(self.model.names))
        if os.path.isfile(config.helmet_classifier_path):
            try:
                self.classifier = HelmetClassifier(config.helmet_classifier_path, device, use_half)
                log.info("[Helmet] Helmet classifier loaded: %s", config.helmet_classifier_path)
            except Exception as e:
                log.warning("[Helmet] Helmet classifier not loaded (%s); using the head detector's classes.", e)
        if self.classifier is None and not self.head_ids:
            log.warning("[Helmet] No bare-head/no-helmet class found; NO HELMET will never be reported.")

    def warmup(self, frame):
        self._warmup(frame[:400, :300], self.config.helmet_imgsz)
        if self.classifier is not None:
            try:
                self.classifier.no_helmet_probs([frame[:40, :40]])
            except Exception as e:
                log_error("Warmup", e)

    @staticmethod
    def _crop_rider_rois(frame, mc_bboxes):
        # The region above each motorcycle where its riders' heads are.
        h, w = frame.shape[:2]
        crops, rois = [], []
        for (mx1, my1, mx2, my2), _ in mc_bboxes:
            mx1, mx2 = min(mx1, mx2), max(mx1, mx2)
            my1, my2 = min(my1, my2), max(my1, my2)
            mw, mh = mx2 - mx1, my2 - my1
            # Expand horizontally (+-15%) and vertically upward (-85% mh) to capture rider head/helmet
            rx1 = max(0, int(mx1 - 0.15 * mw))
            ry1 = max(0, int(my1 - 0.85 * mh))
            rx2 = min(w, int(mx2 + 0.15 * mw))
            ry2 = min(h, int(my1 + 0.65 * mh))
            if (rx2 - rx1) < 20 or (ry2 - ry1) < 20:
                continue
            crop = frame[ry1:ry2, rx1:rx2]
            if crop.size:
                crops.append(crop)
                rois.append((rx1, ry1))
        return crops, rois

    def detect(self, frame, mc_bboxes):
        # (helmet boxes, bare-head boxes, rider boxes) in frame coordinates. Rider boxes are every head at
        # the lower rider_conf; helmet / bare-head ones only those at helmet_conf.
        helmets, heads = [], []
        crops, rois = self._crop_rider_rois(frame, mc_bboxes) if self.model is not None else ([], [])
        if not crops:
            return helmets, heads, []
        results = self._predict_crops(crops, self.config.helmet_imgsz,
                                      conf=min(self.config.helmet_conf, self.config.rider_conf))
        boxes, scores, is_helmet = [], [], []
        for r, (rx1, ry1) in zip(results, rois):
            for bx1, by1, bx2, by2, score, cls in _boxes(r):
                if cls in self.head_ids or cls in self.helmet_ids:
                    boxes.append((rx1 + bx1, ry1 + by1, rx1 + bx2, ry1 + by2))
                    scores.append(score)
                    is_helmet.append(cls in self.helmet_ids)

        # Class-agnostic NMS: one head is one rider, even if the model scored it as both
        # Helmet and No-Helmet (or it appears in two overlapping bike crops)
        kept = suppress_overlapping_boxes(list(range(len(boxes))), scores, boxes_xyxy=boxes)
        riders = [boxes[i] for i in kept]
        sure = [i for i in kept if scores[i] >= self.config.helmet_conf]
        if self.classifier is not None:
            bare = self.classifier.no_helmet_probs([crop_head(frame, boxes[i]) for i in sure]) >= 0.5
        else:
            bare = [not is_helmet[i] for i in sure]
        for i, is_bare in zip(sure, bare):
            (heads if is_bare else helmets).append(boxes[i])
        return helmets, heads, riders


class RiderClassification:
    # The rider classifier (riders and helmet per seat) on each tracked motorcycle, averaged over the
    # track's frames (RiderVoting).

    def __init__(self, config, device, use_half):
        self.config = config
        self.classifier = None
        if os.path.isfile(config.rider_model_path):
            try:
                self.classifier = RiderClassifier(config.rider_model_path, device, use_half)
                log.info("[Rider] Classifier loaded: %s", config.rider_model_path)
            except Exception as e:
                log.warning("[Rider] Classifier not loaded (%s); using the head detector and helmet classifier.", e)
        self.voting = RiderVoting(
            window=config.rider_vote_window,
            min_frames=config.rider_vote_min_frames,
            max_age_s=config.motorcycle_track_max_age_s,
            seat_threshold=config.rider_seat_threshold,
        )

    @property
    def available(self) -> bool:
        return self.classifier is not None

    def reset(self):
        # Drop per-track votes (track IDs restart with the media session).
        self.voting.reset()

    def warmup(self, frame):
        if self.classifier is None:
            return
        try:
            self.classifier.predict_probs([frame[:300, :200]])
        except Exception as e:
            log_error("Warmup", e)

    def _crop_motorcycles(self, frame, mc_ids):
        # Crops of the motorcycles and their riders (5% padding, rider_crop_pad_up above for the heads) with
        # their track IDs. Crops shorter than rider_min_crop_h are skipped.
        h, w = frame.shape[:2]
        crops, tids = [], []
        for (x1, y1, x2, y2), tid in mc_ids.items():
            pad_w, pad_h = int((x2 - x1) * 0.05), int((y2 - y1) * 0.05)
            pad_up = int((y2 - y1) * self.config.rider_crop_pad_up)
            crop = frame[max(0, y1 - pad_up):min(h, y2 + pad_h), max(0, x1 - pad_w):min(w, x2 + pad_w)]
            if crop.shape[0] >= self.config.rider_min_crop_h and crop.shape[1] >= 24:
                crops.append(crop)
                tids.append(tid)
        return crops, tids

    def classify(self, frame, mc_ids, det_lock) -> dict:
        # Classify every tracked motorcycle (mc_ids: box -> track ID) and update its smoothed votes.
        # Returns {track_id: summary or None}. Votes are shared with the processing thread's resets, hence
        # det_lock.
        crops, tids = self._crop_motorcycles(frame, mc_ids)
        if not crops:
            return {}
        with torch.inference_mode():
            probs = self.classifier.predict_probs(crops)
        with det_lock:
            for tid, p, crop in zip(tids, probs, crops):
                self.voting.update(tid, p, weight=crop.shape[0])
            self.voting.prune()
            return {tid: self.voting.get(tid) for tid in tids}

