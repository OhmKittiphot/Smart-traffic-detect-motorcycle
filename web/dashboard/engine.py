"""Runs the detection pipeline (src/) inside the web server process, while staff have the live view open.

One DetectionService per process: models and the OCR process load once, and switching between the live
camera and a video file only replaces the video stream. Its parts live in their own modules: which sources
exist (sources.py), the pictures the live view shows (live_frames.py), saving each motorcycle reported
(recorder.py) and the speed calibration of each source (calibrations.py).
"""
import gc
import logging
import threading
import time
from dataclasses import replace
from typing import Optional

from django.conf import settings
from django.db import close_old_connections

from . import calibrations
from .live_frames import LiveFrames
from .recorder import ReportRecorder
from .sources import VideoSource

log = logging.getLogger("dashboard")

# Opened instead of the IP camera when RTSP_URL is not set: the pipeline still starts, frames never arrive
_UNCONFIGURED_CAMERA = "rtsp://unconfigured.invalid/"


def _health(state: str, label: str, detail: str = "") -> dict:
    return {"state": state, "label": label, "detail": detail}


class DetectionService:
    STARTING, RUNNING, ERROR, STOPPED = "starting", "running", "error", "stopped"

    def __init__(self):
        self.state = self.STARTING
        self.error = ""
        self.source = VideoSource()
        self.app = None      # src DetectionApp, once the models are loaded
        self.config = None   # AppConfig with the violation rules from the settings page
        self.frames = LiveFrames(self)
        self._recorder = ReportRecorder(lambda: self.config, lambda: self.source)
        self._lock = threading.Lock()
        self._started = False

    # ----- lifecycle -----

    def start(self):
        """Load models and start processing in the background; safe to call more than once."""
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._boot, daemon=True, name="DetectionBoot").start()

    def _boot(self):
        from .models import CameraSettings, SystemSettings

        try:
            from app_config import AppConfig, configure_torch_runtime
            from detection_app import DetectionApp
            from report import add_report_listener

            rules = SystemSettings.load()
            self.config = replace(AppConfig.from_env(), max_riders=rules.max_riders,
                                  speed_limit_kmh=float(rules.speed_limit_kmh))
            saved = CameraSettings.load()
            source = VideoSource(saved.mode, saved.video_file if saved.mode == "file" else "",
                                 saved.live_source if saved.mode == "live" else "")
            url = source.capture_url(self.config.rtsp_url)
            if url is None:
                # Saved file or add-on camera no longer exists: fall back to the IP camera
                # (shows No Signal without one)
                source = VideoSource()
                url = source.capture_url(self.config.rtsp_url)
            self.source = source

            device, use_half = configure_torch_runtime()
            if device == "cpu":
                log.warning("CUDA is not available: detection runs on the CPU and will be slow.")
            self.app = DetectionApp(self.config, url or _UNCONFIGURED_CAMERA, device, use_half)
            add_report_listener(self._recorder)
            self._apply_calibration()
            self.app.start_workers()
            self.state = self.RUNNING
            log.info("Detection running on %s (%s).", device, self.source.describe())
        except Exception as e:
            log.exception("Detection failed to start")
            self.state, self.error = self.ERROR, f"{type(e).__name__}: {e}"
        finally:
            close_old_connections()

    def stop(self):
        """Stop the workers (motorcycles still in view are reported first) and stop listening for reports."""
        from report import remove_report_listener

        if self.app is not None:
            self.app.stop()
        remove_report_listener(self._recorder)
        self.state = self.STOPPED

    def apply_rules(self, max_riders: int, speed_limit_kmh: float) -> None:
        """Use violation rules changed on the settings page: they apply to the overlay and to every
        motorcycle reported from now on. A service still loading reads them from the database itself."""
        if self.config is None:
            return
        self.config = replace(self.config, max_riders=max_riders, speed_limit_kmh=speed_limit_kmh)
        if self.app is not None:
            self.app.config = self.config

    def health(self) -> dict:
        """Quick check for the system status page; nothing is started or changed."""
        if self.state == self.STARTING:
            return _health("starting", "Starting", "Loading models to GPU…")
        if self.state == self.ERROR:
            return _health("error", "Error", self.error)
        if self.state == self.STOPPED:
            return _health("standby", "Standby", "Stopping…")
        problems = self.app.worker_problems()
        if problems:
            return _health("problem", "Problem", "; ".join(problems))
        stream = self.app.video_stream
        if not self.has_signal():
            return _health("ok", "Healthy", "No video signal")
        if stream.is_video_file and stream.playback.is_paused():
            return _health("ok", "Healthy", "Video paused")
        fps = self.app.stats.fps()
        if fps == 0 and not stream.playback.holding:
            return _health("problem", "Problem", "Video arrives but no frame is being processed")
        return _health("ok", "Healthy", f"Processing {fps:.1f} fps")

    # ----- source control -----

    def switch(self, source: VideoSource) -> None:
        """Raises ValueError for an invalid choice, RuntimeError while the pipeline is not ready."""
        if self.state != self.RUNNING:
            raise RuntimeError("Detection is not ready yet")
        url = source.capture_url(self.config.rtsp_url)
        if url is None:
            raise ValueError("The selected video file was not found" if source.is_file
                             else "The selected camera is not available")
        with self._lock:
            self.app.switch_source(url or _UNCONFIGURED_CAMERA)
            self.source = source
            self._apply_calibration()
        log.info("Source switched to %s.", source.describe())

    def _file_playback(self, action: str):
        if self.state != self.RUNNING or not self.source.is_file:
            raise RuntimeError(f"Only a video file can be {action}")
        return self.app.video_stream.playback

    def toggle_pause(self, paused: Optional[bool] = None) -> bool:
        return self._file_playback("paused").toggle_pause(paused)

    def seek(self, pos_ms: float) -> None:
        self._file_playback("seeked").seek(pos_ms)

    def camera_configured(self) -> bool:
        return self.source.is_configured(self.config.rtsp_url if self.config else "")

    # ----- speed calibration (one per video source, see calibrations.py) -----

    def _apply_calibration(self) -> None:
        """Give the speed estimator the saved calibration of the current source (or none)."""
        calib = calibrations.for_estimator(self.source.key, self.config)
        estimator = self.app.speed_estimator
        with self.app.det_lock:
            if calib is None:
                estimator.clear_calibration()
                return
            try:
                estimator.set_calibration(*calib)
            except ValueError as e:
                log.warning("Saved speed calibration for %s is invalid (%s).", self.source.key, e)
                estimator.clear_calibration()

    def calibration(self) -> dict:
        """Current source's calibration for the editor (points in frame pixels)."""
        running = self.state == self.RUNNING
        saved = calibrations.to_dict(calibrations.saved(self.source.key))
        return {"calibrated": bool(running and self.app.speed_estimator.calibrated),
                "source": self.source.describe(),
                **(saved or {"points": None, "width_m": None, "length_m": None})}

    def save_calibration(self, points, width_m: float, length_m: float, user) -> None:
        """Raises ValueError when the points do not form a usable rectangle on the road."""
        if self.state != self.RUNNING:
            raise RuntimeError("Detection is not ready yet")
        with self.app.det_lock:
            # Validates the shape (convex, in order, not tiny) before anything is stored
            self.app.speed_estimator.set_calibration([tuple(p) for p in points], (width_m, length_m))
        calibrations.save(self.source.key, points, width_m, length_m, user)

    def remove_calibration(self, user) -> None:
        if self.state != self.RUNNING:
            raise RuntimeError("Detection is not ready yet")
        calibrations.remove(self.source.key, user)
        with self.app.det_lock:
            self.app.speed_estimator.clear_calibration()

    def reload_calibration(self, source_key: str) -> None:
        """Apply a calibration changed on the system status page, if it is the current source's."""
        if self.state == self.RUNNING and source_key == self.source.key:
            self._apply_calibration()

    # ----- status -----

    def has_signal(self) -> bool:
        if self.state != self.RUNNING or not self.camera_configured():
            return False
        stream = self.app.video_stream
        if stream.is_video_file:
            # A paused file keeps its last frame on screen
            return stream.is_running and stream.frames.age() != float("inf")
        return stream.frames.age() < settings.NO_SIGNAL_AFTER_S

    def status(self) -> dict:
        running = self.state == self.RUNNING
        stream = self.app.video_stream if running else None
        playback = stream.playback if stream and stream.is_video_file else None
        pos_ms, total_ms = playback.position() if playback else (0.0, 0.0)
        holding = bool(stream and stream.playback.holding)
        lag = self.app.stats.lag_s(stream) if running and self.has_signal() else 0.0
        if self.state == self.STARTING:
            loading = "Loading models to GPU…"
        elif holding:
            loading = "Processing frame…"
        elif lag > settings.LOADING_LAG_S:
            loading = "GPU is catching up…"
        else:
            loading = ""
        return {
            "state": self.state,
            "error": self.error,
            "mode": self.source.mode,
            "source": self.source.describe(),
            "signal": self.has_signal(),
            "paused": bool(playback and playback.is_paused()),
            "fps": round(self.app.stats.fps(), 1) if running else 0.0,
            "speed_calibrated": bool(running and self.app.speed_estimator.calibrated),
            "camera_configured": self.camera_configured(),
            "pos_ms": round(pos_ms),
            "total_ms": round(total_ms),
            "loading": loading,
        }


_service: Optional[DetectionService] = None
_service_lock = threading.Lock()


# Detection runs only while staff have the live view open: the page polls /api/status/ every second and
# keeps /stream/ open, and stops both when it is left or its tab is hidden. Once neither has been seen
# for this long, detection stops and the GPU is freed.
IDLE_STOP_S = 60
_IDLE_CHECK_EVERY_S = 5
# A live view page polls every second; one not heard from for this long is no longer open
_VIEWER_GONE_S = 5
_last_viewer_t = 0.0
# Session key -> when that session's live view was last seen, so one account logging out does not stop
# detection for the others still watching
_viewers: dict = {}
_idle_watch: Optional[threading.Thread] = None


def get_service(viewer: Optional[str] = None) -> DetectionService:
    """The process-wide service for the live view, started if it is off. Only live-view requests call
    this: each call counts as staff being on the live view (see IDLE_STOP_S). viewer: the session key
    of the page asking, if known."""
    global _service, _last_viewer_t, _idle_watch
    with _service_lock:
        _last_viewer_t = time.time()
        if viewer:
            _viewers[viewer] = _last_viewer_t
        if _service is None:
            _service = DetectionService()
            _service.start()
        if _idle_watch is None:
            _idle_watch = threading.Thread(target=_stop_when_idle, daemon=True, name="DetectionIdle")
            _idle_watch.start()
        return _service


def current_service() -> Optional[DetectionService]:
    """The service while detection is on, else None. Never starts it (system status page)."""
    return _service


def engine_health() -> dict:
    """State of detection for the system status page, checked without starting it."""
    service = _service
    if service is None:
        return _health("standby", "Standby", "Starts when staff open the live view")
    return service.health()


def _stop_when_idle() -> None:
    global _service
    while True:
        time.sleep(_IDLE_CHECK_EVERY_S)
        with _service_lock:
            for key in [k for k, t in _viewers.items() if time.time() - t > IDLE_STOP_S]:
                del _viewers[key]
            service = _service
            # A service still loading its models is left to finish, then stopped on a later check
            idle = (service is not None and service.state != service.STARTING
                    and time.time() - _last_viewer_t > IDLE_STOP_S)
            if idle:
                _service = None
        if idle:
            log.info("No one on the live view for %d s: stopping detection.", IDLE_STOP_S)
            _stop_and_free(service)


def stop_on_logout(session_key: Optional[str] = None) -> None:
    """Stop detection right away when the last staff on the live view log out, instead of waiting
    IDLE_STOP_S. A service still loading its models is marked idle, so the idle check stops it as soon
    as it has loaded."""
    global _service, _last_viewer_t
    with _service_lock:
        _viewers.pop(session_key, None)
        now = time.time()
        if any(now - t < _VIEWER_GONE_S for t in _viewers.values()):
            return   # someone else still has the live view open
        _last_viewer_t = 0.0
        service = _service
        if service is None or service.state == service.STARTING:
            return
        _service = None
    log.info("Logged out: stopping detection.")
    _stop_and_free(service)


def _stop_and_free(service: DetectionService) -> None:
    service.stop()
    del service
    _free_gpu_memory()


def _free_gpu_memory() -> None:
    """Return the stopped models' GPU memory (it is only released once nothing refers to them)."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as e:
        log.warning("Could not free GPU memory: %s", e)


def stop_service() -> None:
    """Stop detection on server shutdown, if anyone logged in and started it."""
    if _service is not None:
        _service.stop()
