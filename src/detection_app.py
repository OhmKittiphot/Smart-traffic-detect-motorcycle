import gc
import multiprocessing as mp
import threading
import time

import cv2
import torch

from ocr import ocr_worker
from province import extract_thai_province
from render import draw_text_on_cv_img
from app_config import load_yolo_model


def open_capture(camera_url: str):
    cap = cv2.VideoCapture(camera_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def merge_related_boxes(boxes):
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


class DetectionApp:
    def __init__(self, config, device, device_secondary, use_half):
        self.config = config
        self.device = device
        self.device_secondary = device_secondary
        self.use_half = use_half

        print("Loading YOLO models...")
        self.model = load_yolo_model(config.model_path, device, use_half)
        try:
            self.model_mc = load_yolo_model(config.motorcycle_model_path, device_secondary, use_half)
        except Exception:
            self.model_mc = None

        self.ocr_in_q = mp.Queue(maxsize=2)
        self.ocr_out_q = mp.Queue(maxsize=2)
        self.ocr_proc = mp.Process(target=ocr_worker, args=(self.ocr_in_q, self.ocr_out_q), daemon=True)

        self.state = {
            "latest_frame": None,
            "frame_for_proc": None,
            "latest_detections": [],
            "latest_mc_detections": [],
            # bbox -> (text, conf, timestamp, stable_count)
            "ocr_cache": {},
            "last_ocr_time": 0.0,
        }

        self.frame_lock = threading.Lock()
        self.proc_lock = threading.Lock()
        self.det_lock = threading.Lock()
        self.frame_ready = threading.Event()

    def start(self):
        self.ocr_proc.start()

        print("Waiting for OCR Process...")
        try:
            msg = self.ocr_out_q.get(timeout=60)
            print(f"OCR Process Status: {msg}")
        except Exception:
            print("OCR Process started.")

        threading.Thread(target=self._capture_fn, daemon=True, name="Capture").start()
        threading.Thread(target=self._yolo_fn, daemon=True, name="YOLO").start()
        threading.Thread(target=self._ocr_result_fn, daemon=True, name="OCR-Result").start()

        print("Waiting for camera feed...")
        while True:
            with self.frame_lock:
                if self.state["latest_frame"] is not None:
                    break
            time.sleep(0.1)
        print("Camera feed active! Detection starting... Press 'q' to exit.")

        frame_interval = 1.0 / self.config.display_fps
        frame_counter = 0

        while True:
            loop_start = time.time()

            with self.frame_lock:
                raw = self.state["latest_frame"]
                frame = raw.copy() if raw is not None else None

            if frame is not None:
                frame_counter += 1

                with self.det_lock:
                    dets = list(self.state["latest_detections"])
                    mc_dets = list(self.state.get("latest_mc_detections", []))

                for (mx1, my1, mx2, my2), m_conf in mc_dets:
                    cv2.rectangle(frame, (mx1, my1), (mx2, my2), (255, 100, 0), 2)
                    cv2.putText(frame, f"Motorcycle {m_conf*100:.0f}%", (mx1, max(0, my1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 100, 0), 2)

                for item in dets:
                    (x1, y1, x2, y2), text, conf = item[:3]
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)

                    if text:
                        label = f"{text} ({conf * 100:.0f}%)"
                        frame = draw_text_on_cv_img(frame, label, (x1, y1), font_size=24, text_color=(255, 255, 255), bg_color=(0, 180, 0))

                if frame_counter % 300 == 0:
                    gc.collect()

                win_title = "Motorcycle License Plate Detection"
                cv2.namedWindow(win_title, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(win_title, 1024, 576)
                cv2.imshow(win_title, frame)

            elapsed = time.time() - loop_start
            wait_ms = max(1, int((frame_interval - elapsed) * 1000))
            if cv2.waitKey(wait_ms) & 0xFF == ord("q"):
                break

        try:
            self.ocr_in_q.put(None, timeout=1)
        except Exception:
            pass
        self.ocr_proc.join(timeout=3)
        cv2.destroyAllWindows()
        print("Program exited.")

    def _capture_fn(self):
        cap = open_capture(self.config.camera_url)
        counter = 0
        while True:
            try:
                if not cap.isOpened():
                    cap.release()
                    time.sleep(1)
                    cap = open_capture(self.config.camera_url)
                    continue
                if not cap.grab():
                    time.sleep(0.02)
                    continue
                ret, frame = cap.retrieve()
                if not ret or frame is None:
                    continue
                with self.frame_lock:
                    self.state["latest_frame"] = frame
                counter += 1
                if counter % self.config.yolo_every_n == 0:
                    with self.proc_lock:
                        self.state["frame_for_proc"] = frame
                    self.frame_ready.set()
            except Exception:
                time.sleep(0.5)

    def _get_cached_ocr(self, bbox):
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        now = time.time()
        best_text, best_conf = "", 0.0

        with self.det_lock:
            for k, v in list(self.state["ocr_cache"].items()):
                # support either (text, conf, ts) or (text, conf, ts, stable)
                if len(v) >= 3:
                    text, conf, t_stamp = v[0], v[1], v[2]
                else:
                    continue
                if now - t_stamp > self.config.ocr_cache_ttl_s:
                    continue
                kx1, ky1, kx2, ky2 = k
                kcx, kcy = (kx1 + kx2) / 2, (ky1 + ky2) / 2
                dist = ((cx - kcx) ** 2 + (cy - kcy) ** 2) ** 0.5
                if dist < self.config.ocr_cache_distance and text:
                    best_text, best_conf = text, conf
                    break
        return best_text, best_conf

    def _yolo_fn(self):
        while True:
            self.frame_ready.wait(timeout=2.0)
            self.frame_ready.clear()

            with self.proc_lock:
                frame = self.state["frame_for_proc"]
            if frame is None:
                continue

            try:
                with torch.inference_mode():
                    results = self.model.predict(
                        frame,
                        imgsz=self.config.detection_imgsz,
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
                            device=self.device_secondary,
                            quantize=self.config.inference_quantize,
                        )
                        for r_mc in res_mc:
                            for box_mc in r_mc.boxes:
                                mx1, my1, mx2, my2 = map(int, box_mc.xyxy[0].tolist())
                                m_conf = float(box_mc.conf[0])
                                mc_bboxes.append(((mx1, my1, mx2, my2), m_conf))

                with self.det_lock:
                    self.state["latest_mc_detections"] = mc_bboxes

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
                    x1_p, y1_p = max(0, x1 - pad_w), max(0, y1 - pad_h)
                    x2_p, y2_p = min(w, x2 + pad_w), min(h, y2 + pad_h)

                    crop = frame[y1_p:y2_p, x1_p:x2_p]
                    if crop.size > 0:
                        bboxes.append(((x1, y1, x2, y2), crop))

                if bboxes:
                    current_dets = []
                    for (bb, _) in bboxes:
                        cached_text, cached_conf = self._get_cached_ocr(bb)
                        current_dets.append((bb, cached_text, cached_conf))

                    with self.det_lock:
                        self.state["latest_detections"] = current_dets

                    now = time.time()
                    if now - self.state["last_ocr_time"] >= self.config.ocr_cooldown_s:
                        payload = [(bb, cr.tobytes(), cr.shape, str(cr.dtype)) for bb, cr in bboxes]
                        try:
                            self.ocr_in_q.put_nowait(payload)
                            self.state["last_ocr_time"] = now
                        except Exception:
                            pass
                else:
                    with self.det_lock:
                        self.state["latest_detections"] = []

            except Exception as e:
                print(f"[YOLO Thread Error] {e}")

            time.sleep(0)

    def _ocr_result_fn(self):
        last_printed = ""
        last_printed_time = 0.0

        while True:
            try:
                results = self.ocr_out_q.get(timeout=5.0)
                if not isinstance(results, list):
                    continue

                now = time.time()
                for item in results:
                    if len(item) >= 5:
                        bbox, plate_text, plate_conf, province_text, province_conf = item[:5]
                    elif len(item) == 3:
                        bbox, plate_text, plate_conf = item
                        province_text, province_conf = "", 0.0
                    else:
                        bbox, plate_text = item[:2]
                        plate_conf = 0.0
                        province_text, province_conf = "", 0.0

                    if plate_text and province_text:
                        final_text = f"{plate_text} {province_text}"
                        final_conf = min(plate_conf, province_conf) if province_conf > 0 else plate_conf
                    elif plate_text:
                        final_text = plate_text
                        final_conf = plate_conf
                    else:
                        final_text = province_text
                        final_conf = province_conf

                    if final_text and final_conf >= self.config.ocr_conf_threshold:
                        with self.det_lock:
                            old_text, old_conf, _ = self.state["ocr_cache"].get(bbox, ("", 0.0, 0))
                            if final_conf >= old_conf:
                                self.state["ocr_cache"][bbox] = (final_text, final_conf, now)
                            else:
                                final_text, final_conf = old_text, old_conf

                        if final_text and (final_text != last_printed or (now - last_printed_time) >= 8.0):
                            pct = final_conf * 100
                            province = extract_thai_province(final_text)
                            print(f"\n=========================================")
                            print(f"   License Plate Detected: {final_text}")
                            print(f"   Province: {province if province else 'Province not found'}")
                            print(f"   Confidence: {pct:.1f}%")
                            print(f"=========================================\n")
                            last_printed = final_text
                            last_printed_time = now
            except Exception:
                pass