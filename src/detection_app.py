import threading
import time

import numpy as np
import torch

from .camera_motion import CameraMotion
from .ocr_handler import OCRResultHandler
from .ocr_manager import OCRManager
from .ocr_process import OcrProcess
from .processing_stats import ProcessingStats
from .render import draw_calibration, draw_detections, draw_video_overlay, draw_zone
from .report import print_motorcycle_reports
from .speed import SpeedEstimator, parse_calibration
from .tracker import MotorcycleTracker
from .utils import log, log_error
from .video_stream import VideoStream
from .yolo_pipeline import YoloPipeline
from .zone import DetectionZone


class DetectionApp:
    # Runs detection on one video source in background threads: capture, YOLO + tracking, camera motion
    # and plate OCR. Models stay loaded when the source changes. Shown by DesktopViewer or the web dashboard.

    def __init__(self, config, video_camera: str, device, use_half, start_paused: bool = False,
                 on_file_end=None):
        self.config = config
        self.start_paused = start_paused   # video files open paused (the web dashboard: staff press play)
        # Called (from the capture thread) when a video file has played to its end, e.g. to open the next
        # file of a playlist
        self.on_file_end = on_file_end
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        self.ocr = OcrProcess(config.plate_ocr_model_path)
        self.tracker = MotorcycleTracker(
            max_age_s=config.motorcycle_track_max_age_s,
            distance_threshold=config.motorcycle_track_distance
        )
        self.ocr_manager = OCRManager(
            cache_ttl_s=config.ocr_cache_ttl_s,
            cache_distance=config.ocr_cache_distance,
            lock_stable_count=config.ocr_lock_stable_count,
            ocr_request_interval_s=config.ocr_request_interval_s,
        )
        self.speed_estimator = SpeedEstimator()
        self._init_speed_calibration()
        # Moves the calibration points with the camera in a video file (None: the camera does not move)
        self.camera_motion = None
        # The area motorcycles are detected in (the whole frame until one is set)
        self.detection_zone = DetectionZone()

        # Guards the tracker, OCR manager, speed estimator and latest_detections, shared by the threads
        self.det_lock = threading.Lock()
        self.latest_detections = []
        self.stats = ProcessingStats()
        self._proc_gen = 0
        self._stop_event = threading.Event()

        self.video_stream = self._new_stream(video_camera)
        self.yolo_pipeline = YoloPipeline(
            config=self.config,
            device=device,
            use_half=use_half,
            tracker=self.tracker,
            speed_estimator=self.speed_estimator,
            ocr_manager=self.ocr_manager,
            ocr_in_q=self.ocr.in_q,
            det_lock=self.det_lock,
            zone=self.detection_zone,
        )
        self.ocr_handler = OCRResultHandler(
            ocr_out_q=self.ocr.out_q,
            ocr_manager=self.ocr_manager,
            tracker=self.tracker,
            det_lock=self.det_lock,
            get_media_gen_fn=lambda: self.video_stream.frames.generation,
            ocr_conf_threshold=self.config.ocr_conf_threshold
        )

        self._source_lock = threading.Lock()
        self._workers_started = False
        self._worker_threads = []

    def _init_speed_calibration(self):
        calib = parse_calibration(self.config.speed_calib_points, self.config.speed_calib_world)
        if calib:
            try:
                self.speed_estimator.set_calibration(*calib)
                log.info("[Speed] Calibration loaded from env.")
            except Exception as e:
                log.warning("[Speed] Invalid calibration in env (%s). Press [C] to recalibrate.", e)
        else:
            log.info("[Speed] Not calibrated. Press [C] in the video window to calibrate.")

    def _new_stream(self, video_camera: str, initial_generation: int = 0, paused=None) -> VideoStream:
        return VideoStream(
            video_camera=video_camera,
            display_fps=self.config.display_fps,
            on_end=self._on_video_end,
            on_seek=self._on_video_seek,
            initial_generation=initial_generation,
            start_paused=self.start_paused if paused is None else paused,
        )

    # ----- media session: tracks, OCR votes and speeds of one stretch of video -----

    def _report(self, finished) -> None:
        # Report motorcycles that left (or were still in view at the end). Called without det_lock: a report
        # listener may write to a database and disk (the web dashboard), which must not hold up detection.
        print_motorcycle_reports(finished, self.speed_estimator.calibrated, self.config.report_min_frames)

    def _reset_media_session(self) -> list:
        # Clear tracker / OCR / speed state bound to the playback session (called holding det_lock).
        # Returns the motorcycles still in view, for _report() once the lock is released.
        finished = self.tracker.drain()
        self.tracker.reset()
        self.ocr_manager.reset()
        self.speed_estimator.reset()
        self.yolo_pipeline.reset()
        self.latest_detections = []
        self.stats.forget_last()
        return finished

    def _on_video_end(self, _generation: int):
        # Motorcycles still in view at the last frame are reported before playback restarts
        with self.det_lock:
            finished = self._reset_media_session()
        self._report(finished)
        if self.on_file_end:
            self.on_file_end()

    def _on_video_seek(self, _generation: int):
        # Motorcycles in view before the jump were only partly seen: drop them instead of reporting
        with self.det_lock:
            self._reset_media_session()

    # ----- speed calibration -----

    @property
    def speed_calibrated(self) -> bool:
        return self.speed_estimator.calibrated

    def set_calibration(self, points, world_size) -> None:
        # Measure speeds on the ground rectangle of these 4 image points and its real (width, length) in
        # meters (see SpeedEstimator.set_calibration). Raises ValueError when they do not form a usable one.
        with self.det_lock:
            self.speed_estimator.set_calibration([tuple(p) for p in points], world_size)

    def clear_calibration(self) -> None:
        with self.det_lock:
            self.speed_estimator.clear_calibration()

    def calibration_points(self):
        # Where the calibration points are on the current frame while the camera is followed (they move with
        # it); None for a fixed camera, where they stay where they were clicked.
        if self.camera_motion is None:
            return None
        with self.det_lock:
            return self.speed_estimator.image_points()

    # ----- moving camera -----

    def follow_camera(self, reference_frame) -> None:
        # Move the calibration points with the camera, from the frame they were clicked on;
        # None: keep them where they were clicked (a fixed camera).
        motion = CameraMotion(reference_frame) if reference_frame is not None else None
        with self.det_lock:
            self.camera_motion = motion
            # Back on the frame the points were clicked on, until the first frame is matched
            self.speed_estimator.set_camera_motion(np.eye(3))

    def _camera_fn(self):
        # Matches the newest frame to the frame the calibration points were clicked on. Runs beside the
        # processing thread, which uses the latest match instead of waiting about 20 ms for one every frame.
        last_frame, last_gen = None, None
        while not self._stop_event.is_set():
            motion = self.camera_motion
            frames = self.video_stream.frames   # the stream may be replaced by switch_source() meanwhile
            frame = frames.latest()
            if motion is None or frame is None or frame is last_frame:
                time.sleep(0.2 if motion is None else 0.005)   # a fixed camera, or no new frame yet
                continue
            generation = frames.generation
            if generation != last_gen:
                # After a seek (or a new source) the last frame matched no longer comes right before this one
                motion.restart()
            last_frame, last_gen = frame, generation
            H = motion.update(frame)
            with self.det_lock:
                # Not when follow_camera() was called meanwhile (a new calibration was saved)
                if motion is self.camera_motion:
                    self.speed_estimator.set_camera_motion(H)

    # ----- workers -----

    def _yolo_fn(self):
        while not self._stop_event.is_set():
            frames = self.video_stream.frames   # the stream may be replaced by switch_source() meanwhile
            if not frames.wait_ready(timeout=2.0):
                continue

            frame, frame_t, frame_gen = frames.for_processing()
            if frame is None:
                continue

            current_gen = frames.generation
            finished = []
            with self.det_lock:
                if frame_gen < current_gen:
                    continue
                if frame_gen != self._proc_gen:
                    finished = self._reset_media_session()
                    self._proc_gen = frame_gen
            self._report(finished)

            try:
                dets = self.yolo_pipeline.process_frame(frame, frame_t, frame_gen, current_gen)
                with self.det_lock:
                    self.latest_detections = dets
                self.stats.frame_done(frame_gen, frame_t)
            except torch.cuda.OutOfMemoryError as e:
                # Free cached blocks so the next frame has a chance; skipping one frame is harmless
                torch.cuda.empty_cache()
                log_error("YOLO", e)
            except Exception as e:
                log_error("YOLO", e)
            finally:
                # Releases playback held at the start of a video / after a seek, even if this frame failed
                frames.mark_processed(frame_gen)

    def start_workers(self):
        # Start OCR, capture and processing in the background. Returns once OCR is ready.
        self.ocr.start()
        log.info("Warming up models...")
        self.yolo_pipeline.warmup()
        self.video_stream.start()
        self._worker_threads = [threading.Thread(target=self._yolo_fn, daemon=True, name="YOLO"),
                                threading.Thread(target=self.ocr_handler.run_loop, daemon=True, name="OCR-Result"),
                                threading.Thread(target=self._camera_fn, daemon=True, name="Camera-Motion")]
        for thread in self._worker_threads:
            thread.start()
        self._workers_started = True

    def worker_problems(self) -> list:
        # What has stopped working among the background workers (empty when all are fine).
        problems = [f"{t.name} thread stopped" for t in self._worker_threads if not t.is_alive()]
        problems += [p for p in (self.ocr.problem(), self.video_stream.error) if p]
        return problems

    def switch_source(self, video_camera: str, paused=None):
        # Replace the video source while models and the OCR process keep running. paused: whether a video
        # file opens paused (None: as start_paused says).
        with self._source_lock:
            old = self.video_stream
            old.stop()
            with self.det_lock:
                finished = self._reset_media_session()
            self._report(finished)
            self.stats.clear()
            self.video_stream = self._new_stream(video_camera, initial_generation=old.frames.generation + 1,
                                                 paused=paused)
            if self._workers_started:
                self.video_stream.start()

    def stop(self):
        # Stop all workers and release resources. Safe to call more than once.
        self._stop_event.set()
        self.ocr.release_queues()
        self.ocr_handler.stop()
        self.video_stream.stop()
        with self.det_lock:
            finished = self.tracker.drain()
        self._report(finished)
        self.ocr.stop()

    # ----- drawing -----

    def render_frame(self, show_progress: bool = False):
        # Latest frame with detections drawn on it, or None before the first frame. show_progress adds a
        # video file's progress bar (the desktop window; the web page draws its own).
        stream = self.video_stream
        raw = stream.frames.latest()
        if raw is None:
            return None
        frame = raw.copy()

        with self.det_lock:
            dets = list(self.latest_detections)
            mc_dets = list(self.tracker.latest_mc_detections)
            mc_ids = dict(self.tracker.bbox_to_track_id)
            speeds = {tid: self.speed_estimator.get_speed(tid) for tid in mc_ids.values()}
            helmets = {tid: self.tracker.riders.helmet_status(tid) for tid in mc_ids.values()}
            riders = {tid: self.tracker.riders.rider_count(tid) for tid in mc_ids.values()}
            calib_points = self.speed_estimator.image_points() if self.camera_motion is not None else None
        zone_points = self.detection_zone.points()

        # A drawing failure (bad bbox, font issue...) should cost one overlay, not the app
        try:
            if zone_points:
                draw_zone(frame, zone_points)
            if calib_points:
                draw_calibration(frame, calib_points)
            frame = draw_detections(frame, dets, mc_dets, mc_ids, speeds, helmets,
                                    riders, self.config.max_riders)
            if stream.is_video_file:
                position_ms, total_ms = stream.playback.position()
                frame = draw_video_overlay(frame, position_ms, total_ms, self.stats.fps(),
                                           stream.playback.is_paused(), show_progress=show_progress)
        except Exception as e:
            log_error("Render", e)
        return frame
