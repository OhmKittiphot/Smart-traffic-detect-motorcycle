"""Runs the detection pipeline (src/) inside the web server process, while staff are logged in.

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
from django.utils.translation import gettext as _

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
            source = (VideoSource("file", saved.video_file, playlist=tuple(saved.playlist))
                      if saved.mode == "file" else VideoSource("live", live_key=saved.live_source))
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
            # A video file plays only when staff press play on the live view
            self.app = DetectionApp(self.config, url or _UNCONFIGURED_CAMERA, device, use_half, start_paused=True,
                                    on_file_end=self._play_next_file)
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
            return _health("starting", _("Starting"), _("Loading models to GPU…"))
        if self.state == self.ERROR:
            return _health("error", _("Error"), self.error)
        if self.state == self.STOPPED:
            return _health("standby", _("Standby"), _("Stopping…"))
        problems = self.app.worker_problems()
        if problems:
            return _health("problem", _("Problem"), "; ".join(problems))
        stream = self.app.video_stream
        if not self.has_signal():
            return _health("ok", _("Healthy"), _("No video signal"))
        if stream.is_video_file and stream.playback.is_paused():
            return _health("ok", _("Healthy"), _("Video paused"))
        fps = self.app.stats.fps()
        if fps == 0 and not stream.playback.holding:
            return _health("problem", _("Problem"), _("Video arrives but no frame is being processed"))
        return _health("ok", _("Healthy"), _("Processing %(fps).1f fps") % {"fps": fps})

    # ----- source control -----

    def switch(self, source: VideoSource, paused: Optional[bool] = None) -> None:
        """paused: whether a video file opens paused (None: paused, until staff press play).
        Raises ValueError for an invalid choice, RuntimeError while the pipeline is not ready."""
        if self.state != self.RUNNING:
            raise RuntimeError(_("Detection is not ready yet"))
        url = source.capture_url(self.config.rtsp_url)
        if url is None:
            raise ValueError(_("The selected video file was not found") if source.is_file
                             else _("The selected camera is not available"))
        with self._lock:
            self.app.switch_source(url or _UNCONFIGURED_CAMERA, paused)
            self.source = source
            self._apply_calibration()
        log.info("Source switched to %s.", source.describe())

    def _play_next_file(self) -> None:
        """A video file played to its end: a playlist goes on with its next file, playing, and after its last
        file opens its first one, paused. Switched in a thread of its own, since the switch stops the capture
        thread this is called from."""
        ended = self.source
        following = ended.next_in_playlist()
        if following is None:
            return

        def play():
            if self.source is not ended:
                return   # staff chose another source meanwhile
            try:
                self.switch(following, paused=following.video_file == following.playlist[0])
            except (ValueError, RuntimeError) as e:
                log.warning("The playlist stops: %s", e)

        threading.Thread(target=play, daemon=True, name="PlaylistNext").start()

    def _file_playback(self, error: str):
        if self.state != self.RUNNING or not self.source.is_file:
            raise RuntimeError(error)
        return self.app.video_stream.playback

    def toggle_pause(self, paused: Optional[bool] = None) -> bool:
        return self._file_playback(_("Only a video file can be paused")).toggle_pause(paused)

    def seek(self, pos_ms: float) -> None:
        self._file_playback(_("Only a video file can be seeked")).seek(pos_ms)

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
            raise RuntimeError(_("Detection is not ready yet"))
        with self.app.det_lock:
            # Validates the shape (convex, in order, not tiny) before anything is stored
            self.app.speed_estimator.set_calibration([tuple(p) for p in points], (width_m, length_m))
        calibrations.save(self.source.key, points, width_m, length_m, user)

    def remove_calibration(self, user) -> None:
        if self.state != self.RUNNING:
            raise RuntimeError(_("Detection is not ready yet"))
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
            loading = _("Loading models to GPU…")
        elif holding:
            loading = _("Processing frame…")
        elif lag > settings.LOADING_LAG_S:
            loading = _("GPU is catching up…")
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


# Detection runs (and holds the GPU) only while staff are logged in: the models load when the first of them
# logs in and are freed when the last logs out or their session expires. While it runs:
# - a live camera is detected all the time
# - a video file only while it plays: it opens paused, staff start and stop it with play / pause, and it
#   pauses itself at the end
# Staff already logged in when the server starts are counted too (resume_logged_in_staff, run.py).
_staff: set = set()   # session keys of the staff logged in
# Sessions that end without logging out (the browser was closed and the session expired) are noticed this often
_SESSION_CHECK_S = 30
_session_watch: Optional[threading.Thread] = None


def staff_logged_in(session_key: Optional[str]) -> None:
    """Staff logged in: start detection if it is off, and keep it on until they log out. The only way it
    starts; requests of the pages never start it, so one still running while its user logs out cannot
    start it again."""
    with _service_lock:
        if session_key:
            _staff.add(session_key)
        _start_service()


def _start_service() -> DetectionService:
    """The service, started if it is off (called holding _service_lock)."""
    global _service, _session_watch
    if _service is None:
        _service = DetectionService()
        _service.start()
    if _session_watch is None:
        _session_watch = threading.Thread(target=_watch_sessions, daemon=True, name="DetectionSessions")
        _session_watch.start()
    return _service


def current_service() -> Optional[DetectionService]:
    """The service while detection is on, else None. Never starts it."""
    return _service


def stopped_status() -> dict:
    """DetectionService.status() for while detection is off."""
    return {"state": DetectionService.STOPPED, "error": "", "mode": "", "source": "", "signal": False,
            "paused": False, "fps": 0.0, "speed_calibrated": False, "camera_configured": False, "pos_ms": 0,
            "total_ms": 0, "loading": ""}


def engine_health() -> dict:
    """State of detection for the system status page, checked without starting it."""
    service = _service
    if service is None:
        return _health("standby", _("Standby"), _("Starts when staff log in"))
    return service.health()


def staff_logged_out(session_key: Optional[str]) -> None:
    """Staff logged out: stop detection at once when no other staff are logged in. A service still loading
    its models is left to finish, then stopped by the session check."""
    with _service_lock:
        _staff.discard(session_key)
        service = _take_unneeded_service()
    if service:
        log.info("Last staff logged out: stopping detection.")
        _stop_and_free(service)


def _take_unneeded_service() -> Optional[DetectionService]:
    """The service, cleared, when no staff are left and it has finished loading; else None (called
    holding _service_lock)."""
    global _service
    service = _service
    if _staff or service is None or service.state == service.STARTING:
        return None
    _service = None
    return service


def _live_sessions(keys) -> set:
    """The keys among `keys` whose session still exists and has not expired."""
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    try:
        return set(Session.objects.filter(session_key__in=keys, expire_date__gt=timezone.now())
                   .values_list("session_key", flat=True))
    finally:
        close_old_connections()


def _watch_sessions() -> None:
    """Forget staff whose session ended without logging out, and stop detection when none are left."""
    while True:
        time.sleep(_SESSION_CHECK_S)
        with _service_lock:
            keys = set(_staff)
        live = _live_sessions(keys)
        with _service_lock:
            _staff.difference_update(keys - live)
            service = _take_unneeded_service()
        if service:
            log.info("No staff logged in: stopping detection.")
            _stop_and_free(service)


def resume_logged_in_staff() -> None:
    """On server start: staff whose session is still valid are logged in, so detection starts for them."""
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    User = get_user_model()
    keys = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        user_id = session.get_decoded().get("_auth_user_id")
        user = User.objects.filter(pk=user_id, is_active=True).first() if user_id else None
        if user and user.has_perm("dashboard.use_dashboard"):
            keys.append(session.session_key)
    if keys:
        log.info("%d staff still logged in: starting detection.", len(keys))
        for key in keys:
            staff_logged_in(key)


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
