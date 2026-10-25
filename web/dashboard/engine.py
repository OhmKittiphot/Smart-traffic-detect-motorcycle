"""Runs the detection pipeline (src/) inside the web server process.

One DetectionService per process: models and the OCR process load once, and switching between the live
camera and a video file only replaces the video stream. Finished motorcycles are saved as VehicleRecord.
"""
import gc
import logging
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

log = logging.getLogger("dashboard")


def list_video_files() -> list:
    """Video file names directly inside VIDEO_INPUT_DIR (no sub-folders, no paths)."""
    folder = Path(settings.VIDEO_INPUT_DIR)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in settings.VIDEO_EXTENSIONS)


def resolve_video_file(name: str) -> Optional[Path]:
    """Path of an allowed video file, or None. Only names returned by list_video_files() are accepted,
    so a crafted name (../, absolute path, other drive) can never reach the file system."""
    if not name or name not in list_video_files():
        return None
    return Path(settings.VIDEO_INPUT_DIR) / name


def list_live_sources() -> dict:
    """key -> (label, url) of live sources besides the IP camera, from optional add-ons (itic_cameras.py).
    The IP camera itself is key ''. Clients only ever send a key, so URLs never come from a request."""
    try:
        from . import itic_cameras
    except ImportError:
        return {}
    return itic_cameras.live_sources()


def source_keys() -> set:
    """Keys of every video source that can have a speed calibration (see DetectionService.source_key)."""
    return ({"live:ip"} | {f"live:{key}" for key in list_live_sources()}
            | {f"file:{name}" for name in list_video_files()})


def source_capture_url(source_key: str) -> Optional[str]:
    """Capture URL / file path of a source key ("live:ip", "live:<add-on key>", "file:<name>"), or None
    when it is not configured. URLs only come from .env and the add-ons, never from a request."""
    kind, _, name = source_key.partition(":")
    if kind == "file":
        path = resolve_video_file(name)
        return str(path) if path else None
    if name == "ip":
        from app_config import AppConfig

        return AppConfig.from_env().rtsp_url or None
    extra = list_live_sources().get(name)
    return extra[1] if extra else None


def grab_frame(source_key: str) -> np.ndarray:
    """One raw frame of a source, opened on its own, so a calibration can be drawn while detection is off.
    It is the same full-size frame detection reads. Raises RuntimeError when the source sends nothing."""
    from utils import open_capture

    url = source_capture_url(source_key)
    if not url:
        raise RuntimeError("This video source is not configured")
    cap = open_capture(url)
    try:
        ok, frame = cap.read() if cap.isOpened() else (False, None)
    finally:
        cap.release()
    if not ok or frame is None:
        raise RuntimeError("No video signal from this source")
    return frame


def _placeholder_frame(title: str, subtitle: str = "", detail: str = "") -> np.ndarray:
    """Dark 'No Signal'-style card with a timestamp, sized like a 720p camera frame."""
    h, w = 720, 1280
    frame = np.full((h, w, 3), (24, 22, 20), dtype=np.uint8)
    # Faint scan lines so it reads as a dead camera feed rather than a blank page
    frame[::4] = (30, 28, 26)
    font = cv2.FONT_HERSHEY_DUPLEX
    (tw, th), _ = cv2.getTextSize(title or " ", font, 2.4, 3)
    if title:
        cv2.putText(frame, title, ((w - tw) // 2, h // 2), font, 2.4, (235, 235, 235), 3, cv2.LINE_AA)
    if subtitle:
        subtitle = subtitle.replace("·", "-")   # cv2 fonts are ASCII-only; source labels use "·"
        (sw, _), _ = cv2.getTextSize(subtitle, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame, subtitle, ((w - sw) // 2, h // 2 + th + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (150, 150, 150), 2, cv2.LINE_AA)
    if detail:
        (dw, _), _ = cv2.getTextSize(detail, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame, detail, ((w - dw) // 2, h // 2 + th + 75), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (90, 170, 235), 2, cv2.LINE_AA)
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(frame, stamp, (24, h - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (150, 150, 150), 2, cv2.LINE_AA)
    return frame


def _encode_jpeg(frame: np.ndarray) -> bytes:
    h, w = frame.shape[:2]
    if w > settings.STREAM_MAX_WIDTH:
        scale = settings.STREAM_MAX_WIDTH / w
        frame = cv2.resize(frame, (settings.STREAM_MAX_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else b""


def _health(state: str, label: str, detail: str = "") -> dict:
    return {"state": state, "label": label, "detail": detail}


class DetectionService:
    STARTING, RUNNING, ERROR, STOPPED = "starting", "running", "error", "stopped"

    def __init__(self):
        self.state = self.STARTING
        self.error = ""
        self.mode = "live"
        self.source_name = ""
        self.live_source = ""   # '' = IP camera (RTSP_URL), else a key of list_live_sources()
        self._app = None
        self._config = None
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._jpeg = b""
        self._jpeg_key = None
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
            self._config = replace(AppConfig.from_env(), max_riders=rules.max_riders,
                                   speed_limit_kmh=float(rules.speed_limit_kmh))
            saved = CameraSettings.load()
            source = self._source_for(saved.mode, saved.video_file, saved.live_source)
            if source is None:
                # Saved file or add-on camera no longer exists: fall back to the IP camera
                # (shows No Signal without one)
                saved.mode, saved.video_file, saved.live_source = "live", "", ""
                source = self._source_for("live", "")
            self.mode, self.source_name = saved.mode, saved.video_file
            self.live_source = saved.live_source if saved.mode == "live" else ""

            device, use_half = configure_torch_runtime()
            if device == "cpu":
                log.warning("CUDA is not available: detection runs on the CPU and will be slow.")
            # An unconfigured camera still starts the pipeline on a placeholder; frames just never arrive
            self._app = DetectionApp(self._config, source or "rtsp://unconfigured.invalid/", device, use_half)
            add_report_listener(self._on_report)
            self._load_calibration()
            self._app.start_workers()
            self.state = self.RUNNING
            log.info("Detection running on %s (%s).", device, self.describe_source())
        except Exception as e:
            log.exception("Detection failed to start")
            self.state, self.error = self.ERROR, f"{type(e).__name__}: {e}"
        finally:
            close_old_connections()

    def stop(self):
        """Stop the workers (motorcycles still in view are reported first) and stop listening for reports."""
        from report import remove_report_listener

        if self._app is not None:
            self._app.stop()
        remove_report_listener(self._on_report)
        self.state = self.STOPPED

    def apply_rules(self, max_riders: int, speed_limit_kmh: float) -> None:
        """Use violation rules changed on the settings page: they apply to the overlay and to every
        motorcycle reported from now on. A service still loading reads them from the database itself."""
        if self._config is None:
            return
        self._config = replace(self._config, max_riders=max_riders, speed_limit_kmh=speed_limit_kmh)
        if self._app is not None:
            self._app.config = self._config

    def health(self) -> dict:
        """Quick check for the system status page; nothing is started or changed."""
        if self.state == self.STARTING:
            return _health("starting", "Starting", "Loading models to GPU…")
        if self.state == self.ERROR:
            return _health("error", "Error", self.error)
        if self.state == self.STOPPED:
            return _health("standby", "Standby", "Stopping…")
        problems = self._app.worker_problems()
        if problems:
            return _health("problem", "Problem", "; ".join(problems))
        stream = self._app.video_stream
        if not self.has_signal():
            return _health("ok", "Healthy", "No video signal")
        if stream.is_video_file and stream.is_paused():
            return _health("ok", "Healthy", "Video paused")
        fps = self._app.processing_fps()
        if fps == 0 and not stream.holding:
            return _health("problem", "Problem", "Video arrives but no frame is being processed")
        return _health("ok", "Healthy", f"Processing {fps:.1f} fps")

    # ----- source control -----

    def _source_for(self, mode: str, video_file: str, live_source: str = "") -> Optional[str]:
        """Capture URL/path for a mode. '' for live without a configured camera, None if invalid."""
        if mode == "live":
            if live_source:
                extra = list_live_sources().get(live_source)
                return extra[1] if extra else None
            return (self._config.rtsp_url if self._config else "") or ""
        path = resolve_video_file(video_file)
        return str(path) if path else None

    def switch(self, mode: str, video_file: str = "", live_source: str = "") -> None:
        """Raises ValueError for an invalid choice, RuntimeError while the pipeline is not ready."""
        if self.state != self.RUNNING:
            raise RuntimeError("Detection is not ready yet")
        source = self._source_for(mode, video_file, live_source)
        if source is None:
            raise ValueError("The selected video file was not found" if mode == "file"
                             else "The selected camera is not available")
        with self._lock:
            self._app.switch_source(source or "rtsp://unconfigured.invalid/")
            self.mode, self.source_name = mode, video_file if mode == "file" else ""
            self.live_source = live_source if mode == "live" else ""
            self._load_calibration()
        log.info("Source switched to %s.", self.describe_source())

    def toggle_pause(self, paused: Optional[bool] = None) -> bool:
        if self.state != self.RUNNING or self.mode != "file":
            raise RuntimeError("Only a video file can be paused")
        return self._app.video_stream.toggle_pause(paused)

    def seek(self, pos_ms: float) -> None:
        if self.state != self.RUNNING or self.mode != "file":
            raise RuntimeError("Only a video file can be seeked")
        self._app.seek(pos_ms)

    def _live_label(self) -> str:
        """Label of the selected add-on camera, '' for the IP camera."""
        extra = list_live_sources().get(self.live_source) if self.live_source else None
        return extra[0] if extra else ""

    def _camera_configured(self) -> bool:
        return bool(self.live_source or (self._config and self._config.rtsp_url))

    def describe_source(self) -> str:
        if self.mode == "file":
            return f"File: {self.source_name}"
        return f"Live: {self._live_label()}" if self.live_source else "Live camera (RTSP)"

    # ----- speed calibration (one per video source) -----

    def source_key(self) -> str:
        if self.mode == "file":
            return f"file:{self.source_name}"
        return f"live:{self.live_source or 'ip'}"

    def _load_calibration(self) -> None:
        """Apply the saved calibration of the current source. The IP camera falls back to
        SPEED_CALIB_POINTS / SPEED_CALIB_WORLD from .env until one is saved from the dashboard."""
        from speed import parse_calibration

        from .models import SpeedCalibration

        saved = SpeedCalibration.objects.filter(source_key=self.source_key()).first()
        calib = None
        if saved is not None and saved.points:
            calib = ([tuple(p) for p in saved.points], (saved.width_m, saved.length_m))
        elif saved is None and self.source_key() == "live:ip":
            calib = parse_calibration(self._config.speed_calib_points, self._config.speed_calib_world)

        estimator = self._app.speed_estimator
        with self._app.det_lock:
            if calib is None:
                estimator.clear_calibration()
                return
            try:
                estimator.set_calibration(*calib)
            except ValueError as e:
                log.warning("Saved speed calibration for %s is invalid (%s).", self.source_key(), e)
                estimator.clear_calibration()

    def calibration(self) -> dict:
        """Current source's calibration for the editor (points in frame pixels)."""
        from .models import SpeedCalibration

        saved = SpeedCalibration.objects.filter(source_key=self.source_key()).first()
        running = self.state == self.RUNNING
        out = {"calibrated": bool(running and self._app.speed_estimator.calibrated),
               "source": self.describe_source(), "points": None, "width_m": None, "length_m": None}
        if saved is not None and saved.points:
            out.update(points=saved.points, width_m=saved.width_m, length_m=saved.length_m)
        return out

    def calibration_frame(self) -> bytes:
        """The raw current frame (no overlays, full resolution) as JPEG, for clicking points on."""
        if not self.has_signal():
            raise RuntimeError("No video signal to calibrate on")
        raw, _, _ = self._app.video_stream.get_display_data()
        ok, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError("Could not capture a frame")
        return buf.tobytes()

    def frame_size(self):
        """(width, height) of the current frame, or None."""
        raw, _, _ = self._app.video_stream.get_display_data() if self.state == self.RUNNING else (None, 0, 0)
        return (raw.shape[1], raw.shape[0]) if raw is not None else None

    def save_calibration(self, points, width_m: float, length_m: float, user) -> None:
        """Raises ValueError when the points do not form a usable rectangle on the road."""
        from .models import SpeedCalibration

        if self.state != self.RUNNING:
            raise RuntimeError("Detection is not ready yet")
        with self._app.det_lock:
            # Validates the shape (convex, in order, not tiny) before anything is stored
            self._app.speed_estimator.set_calibration([tuple(p) for p in points], (width_m, length_m))
        SpeedCalibration.objects.update_or_create(
            source_key=self.source_key(),
            defaults={"points": points, "width_m": width_m, "length_m": length_m, "updated_by": user})

    def remove_calibration(self, user) -> None:
        from .models import SpeedCalibration

        if self.state != self.RUNNING:
            raise RuntimeError("Detection is not ready yet")
        # Kept as an empty row so the .env fallback is not applied again after a restart
        SpeedCalibration.objects.update_or_create(
            source_key=self.source_key(),
            defaults={"points": None, "width_m": None, "length_m": None, "updated_by": user})
        with self._app.det_lock:
            self._app.speed_estimator.clear_calibration()

    def reload_calibration(self, source_key: str) -> None:
        """Apply a calibration changed on the system status page, if it is the current source's."""
        if self.state == self.RUNNING and source_key == self.source_key():
            self._load_calibration()

    # ----- status & frames -----

    def has_signal(self) -> bool:
        if self.state != self.RUNNING:
            return False
        stream = self._app.video_stream
        if self.mode == "live" and not self._camera_configured():
            return False
        if stream.is_video_file:
            # A paused file keeps its last frame on screen
            return stream.is_running and stream.last_frame_age() != float("inf")
        return stream.last_frame_age() < settings.NO_SIGNAL_AFTER_S

    def status(self) -> dict:
        running = self.state == self.RUNNING
        stream = self._app.video_stream if running else None
        pos_ms, total_ms = stream.get_position() if stream and stream.is_video_file else (0.0, 0.0)
        holding = bool(stream and stream.holding)
        lag = self._app.detection_lag_s() if running and self.has_signal() else 0.0
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
            "mode": self.mode,
            "source": self.describe_source(),
            "signal": self.has_signal(),
            "paused": bool(stream and stream.is_video_file and stream.is_paused()),
            "fps": round(self._app.processing_fps(), 1) if running else 0.0,
            "speed_calibrated": bool(running and self._app.speed_estimator.calibrated),
            "camera_configured": self._camera_configured(),
            "pos_ms": round(pos_ms),
            "total_ms": round(total_ms),
            "loading": loading,
        }

    def jpeg(self) -> bytes:
        """Current annotated frame as JPEG (shared by all viewers), or a placeholder card."""
        with self._render_lock:
            if self.state == self.STARTING:
                # No text: the page shows its loading animation in the middle of the video instead
                key, make = ("starting", int(time.time())), lambda: _placeholder_frame("")
            elif self.state == self.ERROR:
                key, make = ("error", int(time.time())), lambda: _placeholder_frame(
                    "SYSTEM ERROR", "See server console")
            elif not self.has_signal():
                configured = not (self.mode == "live" and not self._camera_configured())
                sub = self.describe_source() if configured else "Camera not configured (RTSP_URL)"
                # e.g. the stream's playlist stopped updating (see VideoStream.offline_reason)
                detail = (self._app.video_stream.offline_reason or "") if configured else ""
                key, make = (("nosignal", sub, detail, int(time.time())),
                             lambda: _placeholder_frame("NO SIGNAL", sub, detail))
            else:
                raw, _, _ = self._app.video_stream.get_display_data()
                # Re-render only when the frame or the detections changed
                key = (id(raw), id(self._app.latest_detections), id(self._app.tracker.latest_mc_detections))
                make = self._app.render_frame
            if key != self._jpeg_key:
                frame = make()
                if frame is not None:
                    self._jpeg = _encode_jpeg(frame)
                    self._jpeg_key = key
            return self._jpeg

    # ----- reports -----

    def _on_report(self, meta: dict, speed_calibrated: bool) -> None:
        """Called from the processing thread for each motorcycle that left the scene."""
        from report import find_violations, median_speed

        from .models import VehicleRecord

        try:
            violations = find_violations(meta, speed_calibrated, self._config.max_riders,
                                         self._config.speed_limit_kmh)
            helmet = meta.get("helmet")
            if helmet == (0, 0):
                helmet = None   # no head seen clearly enough: unknown, same as the console report
            speed = median_speed(meta) if speed_calibrated else None
            VehicleRecord.objects.create(
                detected_at=timezone.now(),
                source_mode=self.mode,
                source_name=self.source_name or self._live_label(),
                track_id=meta["track_id"],
                plate_text=(meta.get("plate_text") or "")[:32],
                province_text=(meta.get("province_text") or "")[:64],
                plate_conf=meta.get("plate_conf"),
                riders=meta.get("riders"),
                helmet_count=helmet[0] if helmet else None,
                no_helmet_count=helmet[1] if helmet else None,
                speed_kmh=round(speed, 1) if speed is not None else None,
                violations=",".join(violations),
                is_violation=bool(violations),
                snapshot=self._save_snapshot(meta.get("snapshot")),
                plate_snapshot=self._save_snapshot(meta.get("plate_snapshot")),
            )
        finally:
            close_old_connections()

    @staticmethod
    def _save_snapshot(crop) -> str:
        """Save an evidence photo, scaled down to SNAPSHOT_MAX_SIDE to save disk space."""
        if crop is None or not getattr(crop, "size", 0):
            return ""
        h, w = crop.shape[:2]
        scale = settings.SNAPSHOT_MAX_SIDE / max(h, w)
        if scale < 1:
            crop = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        rel = Path("snapshots") / datetime.now().strftime("%Y%m%d") / f"{uuid.uuid4().hex}.jpg"
        dest = Path(settings.MEDIA_ROOT) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, settings.SNAPSHOT_JPEG_QUALITY])
        if not ok:
            return ""
        dest.write_bytes(buf.tobytes())
        return rel.as_posix()


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
