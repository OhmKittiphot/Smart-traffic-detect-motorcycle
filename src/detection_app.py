import gc
import multiprocessing as mp
import queue
import threading
import time
from collections import deque

import cv2
import torch

from calibration import CalibrationController
from ocr import ocr_worker
from ocr_handler import OCRResultHandler
from ocr_manager import OCRManager
from render import draw_detections, draw_video_overlay
from report import print_motorcycle_reports
from speed import SpeedEstimator, parse_calibration
from tracker import MotorcycleTracker
from utils import log_error
from video_stream import VideoStream
from yolo_pipeline import YoloPipeline


# First launch can be slow while PaddleOCR downloads/loads its models
OCR_STARTUP_TIMEOUT_S = 120.0
FIRST_FRAME_TIMEOUT_S = 30.0


class DetectionApp:
    """Main application orchestrator coordinating video capture, YOLO inference, OCR, and UI display."""

    def __init__(self, config, video_camera: str, device, use_half):
        self.config = config
        self.video_camera = video_camera
        self.device = device
        self.use_half = use_half

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # IPC Queues and Process for OCR
        self.ocr_in_q = mp.Queue(maxsize=2)
        self.ocr_out_q = mp.Queue(maxsize=2)
        self.ocr_proc = mp.Process(target=ocr_worker, args=(self.ocr_in_q, self.ocr_out_q), daemon=True)

        # Core sub-components
        self.tracker = MotorcycleTracker(
            max_age_s=config.motorcycle_track_max_age_s,
            distance_threshold=config.motorcycle_track_distance
        )
        self.ocr_manager = OCRManager(
            cache_ttl_s=config.ocr_cache_ttl_s,
            cache_distance=config.ocr_cache_distance,
            lock_confidence=config.ocr_lock_confidence,
            lock_stable_count=config.ocr_lock_stable_count,
        )
        self.speed_estimator = SpeedEstimator()
        self._init_speed_calibration()

        self.det_lock = threading.Lock()
        self.latest_detections = []
        self._proc_gen = 0
        self._stop_event = threading.Event()

        # Video capture stream
        self.video_stream = VideoStream(
            video_camera=self.video_camera,
            display_fps=self.config.display_fps,
            on_loop=self._on_video_loop
        )

        # Interactive calibration controller
        self.calibration_controller = CalibrationController(
            speed_estimator=self.speed_estimator,
            on_pause=self._on_calib_pause,
            on_resume=self._on_calib_resume
        )

        # YOLO detection pipeline
        self.yolo_pipeline = YoloPipeline(
            config=self.config,
            device=self.device,
            use_half=self.use_half,
            tracker=self.tracker,
            speed_estimator=self.speed_estimator,
            ocr_manager=self.ocr_manager,
            ocr_in_q=self.ocr_in_q,
            det_lock=self.det_lock
        )

        # OCR result worker
        self.ocr_handler = OCRResultHandler(
            ocr_out_q=self.ocr_out_q,
            ocr_manager=self.ocr_manager,
            tracker=self.tracker,
            det_lock=self.det_lock,
            get_media_gen_fn=lambda: self.video_stream.media_generation,
            ocr_conf_threshold=self.config.ocr_conf_threshold
        )

        self._fps_samples = deque(maxlen=30)

    def _init_speed_calibration(self):
        calib = parse_calibration(self.config.speed_calib_points, self.config.speed_calib_world)
        if calib:
            try:
                self.speed_estimator.set_calibration(*calib)
                print("[Speed] Calibration loaded from env.")
            except Exception as e:
                print(f"[Speed] Invalid calibration in env ({e}). Press [C] to recalibrate.")
        else:
            print("[Speed] Not calibrated. Press [C] in the video window to calibrate.")

    def _on_calib_pause(self):
        if self.video_stream.is_video_file and not self.video_stream.is_paused():
            self.video_stream.toggle_pause()

    def _on_calib_resume(self):
        if self.video_stream.is_video_file and self.video_stream.is_paused():
            self.video_stream.toggle_pause()

    def _report_remaining(self):
        """Print reports for motorcycles still in view (end of video loop / shutdown)."""
        print_motorcycle_reports(self.tracker.drain(), self.speed_estimator.calibrated,
                                 self.config.report_min_frames)

    def _reset_media_session(self):
        """Clear tracker / OCR / speed state bound to the playback session."""
        self._report_remaining()
        self.tracker.reset()
        self.ocr_manager.reset()
        self.speed_estimator.reset()
        self.latest_detections = []

    def _on_video_loop(self, _cur_gen: int):
        with self.det_lock:
            self._reset_media_session()

    def _yolo_fn(self):
        while not self._stop_event.is_set():
            if not self.video_stream.wait_frame_ready(timeout=2.0):
                continue

            frame, frame_t, frame_gen = self.video_stream.get_proc_data()
            if frame is None:
                continue

            current_gen = self.video_stream.media_generation
            with self.det_lock:
                if frame_gen < current_gen:
                    continue
                if frame_gen != self._proc_gen:
                    self._reset_media_session()
                    self._proc_gen = frame_gen

            try:
                dets = self.yolo_pipeline.process_frame(frame, frame_t, frame_gen, current_gen)
                with self.det_lock:
                    self.latest_detections = dets
                    self._fps_samples.append(time.perf_counter())
            except torch.cuda.OutOfMemoryError as e:
                # Free cached blocks so the next frame has a chance; skipping one frame is harmless
                torch.cuda.empty_cache()
                log_error("YOLO", e)
            except Exception as e:
                log_error("YOLO", e)

    def _wait_for_ocr(self):
        """Block until the OCR process reports ready; raise if it failed or died."""
        print("Waiting for OCR Process...")
        deadline = time.time() + OCR_STARTUP_TIMEOUT_S
        while time.time() < deadline:
            try:
                msg = self.ocr_out_q.get(timeout=1.0)
            except queue.Empty:
                if not self.ocr_proc.is_alive():
                    raise RuntimeError(f"OCR process exited during startup (exit code {self.ocr_proc.exitcode})")
                continue
            if isinstance(msg, dict) and "error" in msg:
                raise RuntimeError(msg["error"])
            print(f"OCR Process Status: {msg}")
            return
        # First run may still be downloading PaddleOCR models; keep going and let results arrive later
        print(f"[OCR] Not ready after {OCR_STARTUP_TIMEOUT_S:.0f}s; continuing while it loads.")

    def _shutdown(self):
        """Stop all workers and release resources. Safe to call more than once."""
        self._stop_event.set()
        self.ocr_handler.stop()
        self.video_stream.stop()
        with self.det_lock:
            self._report_remaining()
        if self.ocr_proc.is_alive():
            try:
                self.ocr_in_q.put(None, timeout=1)
            except Exception:
                pass
            self.ocr_proc.join(timeout=3)
            if self.ocr_proc.is_alive():
                print("[OCR] Process did not exit in time; terminating.")
                self.ocr_proc.terminate()
                self.ocr_proc.join(timeout=2)
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass

    def start(self):
        try:
            self._run()
        finally:
            self._shutdown()
            print("Program exited.")

    def _run(self):
        self.ocr_proc.start()
        self._wait_for_ocr()

        self.video_stream.start()
        threading.Thread(target=self._yolo_fn, daemon=True, name="YOLO").start()
        threading.Thread(target=self.ocr_handler.run_loop, daemon=True, name="OCR-Result").start()

        print("Waiting for video feed...")
        if not self.video_stream.wait_for_first_frame(timeout=FIRST_FRAME_TIMEOUT_S):
            reason = self.video_stream.error or f"no frame received within {FIRST_FRAME_TIMEOUT_S:.0f}s"
            raise RuntimeError(f"Video source unavailable: {reason}")

        if self.video_stream.is_video_file:
            print("Video loaded! Detection starting...")
            print("  [Space] Pause/Resume  |  [Q] Quit")
        else:
            print("Camera feed active! Detection starting... Press 'q' to exit.")

        frame_counter = 0
        win_title = "Motorcycle License Plate Detection"
        cv2.namedWindow(win_title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win_title, 1024, 576)
        cv2.setMouseCallback(win_title, self.calibration_controller.on_mouse)

        last_raw = None
        last_dets_ref = None
        last_mc_ref = None
        last_paused = None
        window_shown = False

        while True:
            if not self.video_stream.is_running:
                raise RuntimeError(f"Video capture stopped: {self.video_stream.error or 'unknown error'}")

            paused = self.video_stream.is_paused()
            raw, pos_ms, total_ms = self.video_stream.get_display_data()

            dets_ref = self.latest_detections
            mc_ref = self.tracker.latest_mc_detections
            calib_active = self.calibration_controller.is_active

            redraw = raw is not None and (
                raw is not last_raw or dets_ref is not last_dets_ref or mc_ref is not last_mc_ref
                or paused != last_paused or calib_active
            )
            frame = raw.copy() if redraw else None
            if redraw:
                last_raw, last_dets_ref, last_mc_ref, last_paused = raw, dets_ref, mc_ref, paused

            if frame is not None:
                frame_counter += 1

                with self.det_lock:
                    dets = list(self.latest_detections)
                    mc_dets = list(self.tracker.latest_mc_detections)
                    mc_ids = dict(self.tracker.bbox_to_track_id)
                    speeds = {tid: self.speed_estimator.get_speed(tid) for tid in mc_ids.values()}
                    helmets = {tid: self.tracker.get_helmet_status(tid) for tid in mc_ids.values()}
                    riders = {tid: self.tracker.get_rider_count(tid) for tid in mc_ids.values()}
                    fps_samples = list(self._fps_samples)

                # A drawing failure (bad bbox, font issue...) should cost one overlay, not the app
                try:
                    if calib_active:
                        frame = self.calibration_controller.draw(self.calibration_controller.frame.copy())
                    else:
                        frame = draw_detections(frame, dets, mc_dets, mc_ids, speeds, helmets,
                                                riders, self.config.max_riders)

                    if self.video_stream.is_video_file:
                        # YOLO frames completed per second, sampled in the YOLO thread
                        span = fps_samples[-1] - fps_samples[0] if len(fps_samples) > 1 else 0.0
                        proc_fps = (len(fps_samples) - 1) / span if span > 0 else 0.0
                        frame = draw_video_overlay(frame, pos_ms, total_ms, proc_fps, paused)
                except Exception as e:
                    log_error("Render", e)

                if frame_counter % 300 == 0:
                    gc.collect()

                cv2.imshow(win_title, frame)
                window_shown = True

            key = cv2.waitKey(1 if frame is not None else 5) & 0xFF
            # Window closed with the X button: treat like [Q] instead of letting imshow reopen it
            if window_shown and cv2.getWindowProperty(win_title, cv2.WND_PROP_VISIBLE) < 1:
                break
            if self.calibration_controller.is_active:
                self.calibration_controller.handle_key(key)
            elif key == ord("q"):
                break
            elif key == ord(" ") and self.video_stream.is_video_file:
                self.video_stream.toggle_pause()
            elif key == ord("c") and raw is not None:
                self.calibration_controller.begin(raw)
