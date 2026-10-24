import gc
import multiprocessing as mp
import os
import threading
import time
from collections import deque
from datetime import timedelta

import cv2
import torch

from ocr import ocr_worker
from province import extract_thai_province
from render import draw_text_on_cv_img
from app_config import load_yolo_model
from utils import format_thai_plate, open_capture, merge_related_boxes
from tracker import MotorcycleTracker
from ocr_manager import OCRManager
from speed import SpeedEstimator, parse_calibration
from rider_classifier import NO_HELMET, RiderClassifier
from rider_voting import RiderVoting


class DetectionApp:
    def __init__(self, config, camera_url: str, device, device_secondary, use_half):
        self.config = config
        self.camera_url = camera_url   # URL or path selected from the menu
        self.device = device
        self.device_secondary = device_secondary
        self.use_half = use_half

        print("Loading YOLO models...")
        self.ocr_enabled = config.enable_plate_ocr
        self.model = None
        self.plate_classes = None
        if self.ocr_enabled:
            self.model = load_yolo_model(config.model_path, device, use_half)
            # Multi-class plate models (e.g. letters/license-plate/province) -> keep only the plate box
            self.plate_classes = [i for i, n in self.model.names.items() if n == "license-plate"] or None
        try:
            self.model_mc = load_yolo_model(config.motorcycle_model_path, device_secondary, use_half)
            # Works for both the custom detector ({0: motorcycle}) and COCO models ({3: motorcycle})
            self.mc_classes = [i for i, n in self.model_mc.names.items() if n in ("motorcycle", "motorbike")] or None
        except Exception as e:
            print(f"[Motorcycle] Could not load detector '{config.motorcycle_model_path}': {e}")
            self.model_mc = None
            self.mc_classes = None

        self.rider_cls = None
        if os.path.exists(config.rider_model_path):
            self.rider_cls = RiderClassifier(config.rider_model_path, device_secondary, use_half)
            print(f"[Rider] Classifier loaded: {config.rider_model_path}")
        else:
            print(f"[Rider] Classifier not found at '{config.rider_model_path}', rider/helmet check disabled.")
        self.rider_voting = RiderVoting(
            window=config.rider_vote_window,
            min_frames=config.rider_vote_min_frames,
            max_age_s=config.motorcycle_track_max_age_s,
        )

        if torch.cuda.is_available():
            # Release cached-but-unused VRAM before spawning the OCR process (also GPU-based)
            torch.cuda.empty_cache()

        if self.ocr_enabled:
            self.ocr_in_q = mp.Queue(maxsize=2)
            self.ocr_out_q = mp.Queue(maxsize=2)
            self.ocr_proc = mp.Process(target=ocr_worker, args=(self.ocr_in_q, self.ocr_out_q), daemon=True)

        # Initialize sub-components
        self.tracker = MotorcycleTracker(
            max_age_s=config.motorcycle_track_max_age_s, 
            distance_threshold=config.motorcycle_track_distance
        )
        
        self.ocr_manager = OCRManager(
            cache_ttl_s=config.ocr_cache_ttl_s,
            cache_distance=config.ocr_cache_distance
        )

        self.speed_estimator = SpeedEstimator()
        calib = parse_calibration(config.speed_calib_points, config.speed_calib_world)
        if calib:
            try:
                self.speed_estimator.set_calibration(*calib)
                print("[Speed] Calibration loaded from env.")
            except Exception as e:
                print(f"[Speed] Invalid calibration in env ({e}). Press [C] to recalibrate.")
        else:
            print("[Speed] Not calibrated. Press [C] in the video window to calibrate.")
        self._calib_pts = None        # list of clicked points while calibrating, else None
        self._calib_frame = None      # frozen frame shown during calibration
        self._calib_w = None          # width (m) once entered
        self._calib_input = ""        # digits typed in the window
        self._calib_msg = ""          # error/info text shown in the window

        self.state = {
            "latest_frame": None,
            "frame_for_proc": None,
            "frame_for_proc_t": 0.0,   # timestamp (s) of frame_for_proc
            "latest_detections": [],
            "video_pos_ms": 0.0,       # current position in the video (ms)
            "video_total_ms": 0.0,     # total duration of the video (ms)
        }

        self.frame_lock = threading.Lock()
        self.proc_lock = threading.Lock()
        self.det_lock = threading.Lock()
        self.frame_ready = threading.Event()

        # For video file only
        self.is_video_file = not camera_url.lower().startswith("rtsp://")
        self._paused = False          # pause/resume state
        self._pause_lock = threading.Lock()
        self._fps_samples = deque(maxlen=30)  # keep samples to compute actual FPS
        self._video_writer = None     # for recording output video
        self._source_fps = float(config.display_fps)  # replaced with the real source FPS once known

    def _is_paused(self) -> bool:
        with self._pause_lock:
            return self._paused

    def _toggle_pause(self):
        with self._pause_lock:
            self._paused = not self._paused
        state = "PAUSED" if self._paused else "RESUMED"
        print(f"[Video] {state}")

    def _on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and self._calib_pts is not None and len(self._calib_pts) < 4:
            self._calib_pts.append((x, y))

    def _begin_calibration(self, frame):
        self._calib_frame = frame.copy()
        self._calib_pts = []
        self._calib_w = None
        self._calib_input = ""
        self._calib_msg = ""
        if self.is_video_file and not self._is_paused():
            self._toggle_pause()
        print("[Speed] Click 4 ground points forming a rectangle on the road (e.g. lane markings):")
        print("        1 far-left, 2 far-right, 3 near-right, 4 near-left.  [Esc] cancel")

    def _end_calibration(self):
        self._calib_pts = None
        self._calib_frame = None
        if self.is_video_file and self._is_paused():
            self._toggle_pause()

    def _draw_calibration(self, frame):
        pts = self._calib_pts or []
        for i, p in enumerate(pts):
            cv2.circle(frame, p, 6, (0, 0, 255), -1)
            cv2.putText(frame, str(i + 1), (p[0] + 8, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
            if i > 0:
                cv2.line(frame, pts[i - 1], p, (0, 255, 255), 2, cv2.LINE_AA)
        if len(pts) == 4:
            cv2.line(frame, pts[3], pts[0], (0, 255, 255), 2, cv2.LINE_AA)
            what = "WIDTH (point 1->2)" if self._calib_w is None else "LENGTH (point 2->3)"
            text = f"Real {what} in meters: {self._calib_input}_  [Enter] ok  [Esc] cancel"
        else:
            text = f"CALIBRATION: click point {len(pts) + 1}/4  [Esc] cancel"
        frame = draw_text_on_cv_img(frame, text, (10, 96), font_size=22, text_color=(255, 255, 255),
                                    bg_color=(0, 0, 0), bg_alpha=0.7)
        if self._calib_msg:
            frame = draw_text_on_cv_img(frame, self._calib_msg, (10, 132), font_size=22,
                                        text_color=(255, 255, 255), bg_color=(0, 0, 0), bg_alpha=0.7)
        return frame

    def _handle_calib_key(self, key: int):
        if key == 27:
            self._end_calibration()
            print("[Speed] Calibration cancelled.")
            return
        if self._calib_pts is None or len(self._calib_pts) < 4:
            return

        if key in (13, 10):
            try:
                val = float(self._calib_input)
                if val <= 0:
                    raise ValueError
            except ValueError:
                self._calib_msg = "Invalid number, enter a value > 0"
                self._calib_input = ""
                return
            self._calib_input = ""
            self._calib_msg = ""
            if self._calib_w is None:
                self._calib_w = val
            else:
                self._apply_calibration(self._calib_w, val)
        elif key == 8:
            self._calib_input = self._calib_input[:-1]
        elif key != 255 and chr(key) in "0123456789." and len(self._calib_input) < 8:
            self._calib_input += chr(key)

    def _apply_calibration(self, w: float, l: float):
        pts = list(self._calib_pts)
        try:
            self.speed_estimator.set_calibration(pts, (w, l))
        except Exception as e:
            # Let the user re-click instead of leaving the window stuck
            self._calib_pts = []
            self._calib_w = None
            self._calib_msg = f"Calibration failed: {e}. Click 4 points again"
            print(f"[Speed] Calibration failed: {e}")
            return
        print("[Speed] Calibrated. To persist, add to .env:")
        print(f"SPEED_CALIB_POINTS={';'.join(f'{x},{y}' for x, y in pts)}")
        print(f"SPEED_CALIB_WORLD={w},{l}")
        self._end_calibration()

    def _setup_video_writer(self, frame_shape, fps: float):
        """Create a VideoWriter to record the output video"""
        os.makedirs("output", exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join("output", f"detected_{timestamp}.mp4")
        h, w = frame_shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._video_writer = cv2.VideoWriter(out_path, fourcc, fps, (w, h))
        print(f"[Video] Recording output to: {out_path}")
        return out_path

    def _draw_video_overlay(self, frame, pos_ms: float, total_ms: float,
                            proc_fps: float, paused: bool):
        """Draw an overlay showing timestamp, progress bar, and FPS on the frame"""
        h, w = frame.shape[:2]

        # --- Timestamp & FPS (top-left corner) ---
        pos_td = str(timedelta(milliseconds=int(pos_ms))).split(".")[0]
        total_td = str(timedelta(milliseconds=int(total_ms))).split(".")[0]
        time_text = f"{pos_td} / {total_td}"
        fps_text = f"Proc FPS: {proc_fps:.1f}"
        pause_text = "  [PAUSED]" if paused else ""

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (320, 56), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

        cv2.putText(frame, time_text + pause_text, (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, fps_text, (10, 46),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 255, 180), 1, cv2.LINE_AA)

        # --- Progress bar (bottom) ---
        bar_h = 6
        bar_y = h - bar_h
        progress = pos_ms / total_ms if total_ms > 0 else 0
        filled_w = int(w * progress)

        cv2.rectangle(frame, (0, bar_y), (w, h), (60, 60, 60), -1)
        cv2.rectangle(frame, (0, bar_y), (filled_w, h), (0, 200, 120), -1)

        return frame

    def _violation_text(self, summary) -> str:
        """Short violation description for a rider summary, or '' if the motorcycle is OK."""
        parts = []
        if summary["passengers"] > self.config.max_passengers:
            parts.append(f"OVERLOAD {summary['riders']} riders")
        if summary["no_helmet"]:
            parts.append(f"NO HELMET x{summary['no_helmet']}")
        return " | ".join(parts)

    def _report_violation(self, track_id: int, summary) -> None:
        seats = ", ".join(
            f"{pos}:{'no helmet' if s == NO_HELMET else 'helmet'}"
            for pos, s in summary["states"].items() if s
        )
        print("\n=========================================")
        print(f"   VIOLATION  Motorcycle ID {track_id}")
        print(f"   Riders: {summary['riders']}  (passengers: {summary['passengers']})")
        print(f"   Seats: {seats}")
        print(f"   {self._violation_text(summary)}")
        print("=========================================\n")

    def _classify_riders(self, frame, mc_ids):
        """Run the rider/helmet classifier on every tracked motorcycle crop and update the votes."""
        if self.rider_cls is None or not mc_ids:
            return
        h, w = frame.shape[:2]
        crops, tids = [], []
        for (x1, y1, x2, y2), tid in mc_ids.items():
            pad_w, pad_h = int((x2 - x1) * 0.05), int((y2 - y1) * 0.05)
            crop = frame[max(0, y1 - pad_h):min(h, y2 + pad_h), max(0, x1 - pad_w):min(w, x2 + pad_w)]
            if crop.shape[0] >= 24 and crop.shape[1] >= 24:
                crops.append(crop)
                tids.append(tid)
        probs = self.rider_cls.predict_probs(crops)
        with self.det_lock:
            for tid, p in zip(tids, probs):
                self.rider_voting.update(tid, p)
            self.rider_voting.prune()
            summaries = [(tid, self.rider_voting.get(tid)) for tid in tids]
        for tid, summary in summaries:
            if summary and self._violation_text(summary) and self.rider_voting.should_report(tid):
                self._report_violation(tid, summary)

    def start(self):
        if self.ocr_enabled:
            self.ocr_proc.start()

            print("Waiting for OCR Process...")
            try:
                msg = self.ocr_out_q.get(timeout=60)
                print(f"OCR Process Status: {msg}")
            except Exception:
                print("OCR Process started.")

        threading.Thread(target=self._capture_fn, daemon=True, name="Capture").start()
        threading.Thread(target=self._yolo_fn, daemon=True, name="YOLO").start()
        if self.ocr_enabled:
            threading.Thread(target=self._ocr_result_fn, daemon=True, name="OCR-Result").start()

        print("Waiting for video feed...")
        while True:
            with self.frame_lock:
                if self.state["latest_frame"] is not None:
                    break
            time.sleep(0.1)

        if self.is_video_file:
            print("Video loaded! Detection starting...")
            print("  [Space] Pause/Resume  |  [Q] Quit")
        else:
            print("Camera feed active! Detection starting... Press 'q' to exit.")

        frame_counter = 0
        win_title = "Motorcycle Rider & Helmet Detection"
        cv2.namedWindow(win_title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win_title, 1024, 576)
        cv2.setMouseCallback(win_title, self._on_mouse)

        # For video: prepare output writer after getting the first frame
        output_initialized = False
        display_t = time.time()

        while True:
            with self.frame_lock:
                raw = self.state["latest_frame"]
                frame = raw.copy() if raw is not None else None
                pos_ms = self.state["video_pos_ms"]
                total_ms = self.state["video_total_ms"]

            if frame is not None:
                frame_counter += 1

                # Start the video writer for the first time (video file only)
                # Paused
                # if self.is_video_file and not output_initialized:
                #     self._setup_video_writer(frame.shape, self._source_fps)
                #     output_initialized = True

                with self.det_lock:
                    dets = list(self.state["latest_detections"])
                    mc_dets = list(self.tracker.latest_mc_detections)
                    mc_ids = dict(self.tracker.bbox_to_track_id)
                    speeds = {tid: self.speed_estimator.get_speed(tid) for tid in mc_ids.values()}
                    riders = {tid: self.rider_voting.get(tid) for tid in mc_ids.values()}

                if self._calib_pts is not None:
                    frame = self._draw_calibration(self._calib_frame.copy())

                for (mx1, my1, mx2, my2), m_conf in mc_dets:
                    tid = mc_ids.get((mx1, my1, mx2, my2))
                    summary = riders.get(tid)
                    violation = self._violation_text(summary) if summary else ""
                    # Orange = not classified yet, green = OK, red = violation
                    color = (0, 140, 255) if summary is None else ((0, 0, 255) if violation else (0, 200, 0))
                    cv2.rectangle(frame, (mx1, my1), (mx2, my2), color, 2)

                    label_mc = f"#{tid} " if tid is not None else ""
                    if summary:
                        label_mc += f"Riders {summary['riders']} (pax {summary['passengers']})"
                        label_mc += f"  Helmet {summary['riders'] - summary['no_helmet']}/{summary['riders']}"
                    else:
                        label_mc += f"Motorcycle {m_conf*100:.0f}%"
                    spd = speeds.get(tid)
                    if spd is not None:
                        label_mc += f"  {spd:.0f} km/h"
                    frame = draw_text_on_cv_img(frame, label_mc, (mx1, max(30, my1)), font_size=20,
                                                text_color=(255, 255, 255), bg_color=(0, 0, 0), bg_alpha=0.7)
                    if violation:
                        frame = draw_text_on_cv_img(frame, violation, (mx1, min(frame.shape[0] - 4, my2 + 30)),
                                                    font_size=20, text_color=(255, 255, 255), bg_color=(200, 0, 0))

                for item in dets:
                    (x1, y1, x2, y2), text, conf = item[:3]
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)

                    if text:
                        label = f"{text} ({conf * 100:.0f}%)"
                        frame = draw_text_on_cv_img(frame, label, (x1, y1), font_size=24, text_color=(255, 255, 255), bg_color=(0, 180, 0))

                # Draw overlay for video file
                if self.is_video_file:
                    now = time.time()
                    self._fps_samples.append(now)
                    if len(self._fps_samples) >= 2:
                        proc_fps = (len(self._fps_samples) - 1) / (self._fps_samples[-1] - self._fps_samples[0])
                    else:
                        proc_fps = 0.0
                    frame = self._draw_video_overlay(
                        frame, pos_ms, total_ms, proc_fps, self._is_paused()
                    )

                # Write frame to output video
                # Paused
                # if self._video_writer is not None:
                #     self._video_writer.write(frame)

                if frame_counter % 300 == 0:
                    gc.collect()

                cv2.imshow(win_title, frame)

            key = cv2.waitKey(1) & 0xFF
            if self._calib_pts is not None:
                self._handle_calib_key(key)
            elif key == ord("q"):
                break
            elif key == ord(" ") and self.is_video_file:
                self._toggle_pause()
            elif key == ord("c") and frame is not None:
                self._begin_calibration(raw)

        # Close video writer
        # Paused
        # if self._video_writer is not None:
        #     self._video_writer.release()
        #     print("[Video] Output video saved.")

        if self.ocr_enabled:
            try:
                self.ocr_in_q.put(None, timeout=1)
            except Exception:
                pass
            self.ocr_proc.join(timeout=3)
        cv2.destroyAllWindows()
        print("Program exited.")

    def _capture_fn(self):
        cap = open_capture(self.camera_url)

        if self.is_video_file:
            # Pace playback at the source file's native FPS to match real-time playback
            source_fps = cap.get(cv2.CAP_PROP_FPS)
            if source_fps <= 0:
                source_fps = self.config.display_fps
            self._source_fps = source_fps
            frame_interval = 1.0 / source_fps
            total_ms = cap.get(cv2.CAP_PROP_FRAME_COUNT) / source_fps * 1000.0
            with self.frame_lock:
                self.state["video_total_ms"] = total_ms
            print(f"[Video] Playback FPS: {source_fps:.1f} (source)  |  Duration: {str(timedelta(milliseconds=int(total_ms))).split('.')[0]}")
        else:
            frame_interval = 0.0

        grab_fail_streak = 0
        while True:
            # --- Pause: stop reading new frames when paused ---
            if self.is_video_file and self._is_paused():
                time.sleep(0.05)
                continue

            frame_start = time.time()
            try:
                if not cap.isOpened():
                    cap.release()
                    time.sleep(1)
                    cap = open_capture(self.camera_url)
                    if self.is_video_file:
                        source_fps = cap.get(cv2.CAP_PROP_FPS)
                        if source_fps <= 0:
                            source_fps = self.config.display_fps
                        self._source_fps = source_fps
                        frame_interval = 1.0 / source_fps
                    continue

                if not cap.grab():
                    if self.is_video_file:
                        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                        current_frame = cap.get(cv2.CAP_PROP_POS_FRAMES)
                        at_real_end = total_frames > 0 and current_frame >= total_frames - 1
                        grab_fail_streak += 1
                        # A corrupt/undecodable frame mid-stream also fails grab(), so only
                        # treat this as real EOF near the last frame or after repeated failures.
                        if at_real_end or grab_fail_streak >= 30:
                            print("[Video] End of video. Looping...")
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            with self.frame_lock:
                                self.state["video_pos_ms"] = 0.0
                            grab_fail_streak = 0
                        else:
                            time.sleep(0.05)
                    else:
                        # Stream stalled/dropped: reconnect instead of retrying a dead connection
                        print("[RTSP] Stream read failed. Reconnecting...")
                        cap.release()
                        time.sleep(1)
                        cap = open_capture(self.camera_url)
                    continue
                grab_fail_streak = 0

                ret, frame = cap.retrieve()
                if not ret or frame is None:
                    # Bad/undecodable frame: back off briefly instead of busy-spinning
                    time.sleep(0.02)
                    continue

                # Update current position in the video
                if self.is_video_file:
                    pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                    with self.frame_lock:
                        self.state["video_pos_ms"] = pos_ms

                with self.frame_lock:
                    self.state["latest_frame"] = frame
                # Use the media timestamp for files so speed is independent of processing FPS
                frame_t = pos_ms / 1000.0 if self.is_video_file else time.time()
                with self.proc_lock:
                    self.state["frame_for_proc"] = frame
                    self.state["frame_for_proc_t"] = frame_t
                self.frame_ready.set()

            except Exception:
                time.sleep(0.5)
                continue

            # Control speed to match the source video's FPS
            if self.is_video_file:
                elapsed = time.time() - frame_start
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)


    def _yolo_fn(self):
        while True:
            self.frame_ready.wait(timeout=2.0)
            self.frame_ready.clear()

            with self.proc_lock:
                frame = self.state["frame_for_proc"]
                frame_t = self.state["frame_for_proc_t"]
            if frame is None:
                continue

            try:
                with torch.inference_mode():
                    results = []
                    if self.ocr_enabled:
                        results = self.model.predict(
                            frame,
                            imgsz=self.config.detection_imgsz,
                            classes=self.plate_classes,
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
                            classes=self.mc_classes,
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
                    self.tracker.update_tracks(mc_bboxes)
                    if self.speed_estimator.calibrated:
                        for mc_bb, tid in self.tracker.bbox_to_track_id.items():
                            self.speed_estimator.update(tid, mc_bb, frame_t)
                        self.speed_estimator.prune(frame_t)
                    mc_ids = dict(self.tracker.bbox_to_track_id)

                self._classify_riders(frame, mc_ids)

                if not self.ocr_enabled:
                    continue

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

                if bboxes:
                    current_dets = []
                    ocr_needed = []
                    with self.det_lock:
                        for (bb, cr) in bboxes:
                            cached_text, cached_conf = self.ocr_manager.get_cached_ocr(bb)
                            current_dets.append((bb, cached_text, cached_conf))
                            if not cached_text:
                                ocr_needed.append((bb, cr))
                        
                        self.state["latest_detections"] = current_dets

                    if ocr_needed:
                        payload = [
                            (bb, cr.tobytes(), cr.shape, str(cr.dtype))
                            for bb, cr in ocr_needed
                        ]
                        try:
                            self.ocr_in_q.put_nowait(payload)
                        except Exception:
                            pass
                else:
                    with self.det_lock:
                        self.state["latest_detections"] = []

            except Exception as e:
                print(f"[YOLO Thread Error] {e}")

            time.sleep(0)

    def _ocr_result_fn(self):
        while True:
            try:
                results = self.ocr_out_q.get(timeout=5.0)
                if not isinstance(results, list):
                    continue

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

                    plate_text = str(plate_text or "").strip()
                    province_text = str(province_text or "").strip()
                    
                    plate_text, plate_conf, province_text, province_conf = self.ocr_manager.aggregate_vote_result(
                        bbox,
                        plate_text,
                        float(plate_conf) if plate_conf else 0.0,
                        province_text,
                        float(province_conf) if province_conf else 0.0,
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

                    if final_text and final_conf >= self.config.ocr_conf_threshold:
                        with self.det_lock:
                            final_text, final_conf = self.ocr_manager.update_cache(bbox, final_text, final_conf)

                            track_id = self.tracker.resolve_track_id(bbox)
                            if track_id is not None:
                                self.ocr_manager.add_motorcycle_ocr(track_id, final_text, final_conf)
                                consensus = self.ocr_manager.get_motorcycle_consensus(track_id)
                                
                                if consensus["plate_text"]:
                                    print(f"\nMotorcycle ID {track_id}")
                                    print(f"  Frame: {consensus.get('latest_timestamp', 0)}")
                                    print(f"  OCR count: {consensus['ocr_count']}")
                                    print(f"  Consensed plate: {consensus['plate_text']}")
                                    print(f"  Confidence: {consensus['consensus_conf'] * 100:.1f}%")
                                    print("-----------------------------------------")

                        if final_text:
                            pct = final_conf * 100
                            province = extract_thai_province(final_text)
                            formatted_plate = format_thai_plate(plate_text, province)
                            print(f"\n=========================================")
                            print(f"   License Plate Detected: {formatted_plate}")
                            print(f"   Province: {province if province else 'Province not found'}")
                            print(f"   Confidence: {pct:.1f}%")
                            print(f"=========================================\n")
            except Exception:
                pass