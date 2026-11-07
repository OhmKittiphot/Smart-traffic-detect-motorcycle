import gc

import cv2

from .calibration import CalibrationController
from .utils import log_error

FIRST_FRAME_TIMEOUT_S = 30.0
_WINDOW_TITLE = "Motorcycle License Plate Detection"


class DesktopViewer:
    # Shows a DetectionApp in an OpenCV window (python -m src.main): [Space] pause, [C] calibrate speed, [Q] quit.

    def __init__(self, app):
        self.app = app
        self.calibration = CalibrationController(
            set_calibration=app.set_calibration,
            on_pause=lambda: self._set_paused(True),
            on_resume=lambda: self._set_paused(False),
        )

    def _set_paused(self, paused: bool) :
        stream = self.app.video_stream
        if stream.is_video_file and stream.playback.is_paused() != paused:
            stream.playback.toggle_pause()

    def run(self):
        # Start detection and show it until the window is closed; detection is stopped on the way out.
        try:
            self._run()
        finally:
            self.app.stop()
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass
            print("Program exited.")

    @staticmethod
    def _wait_for_video(stream):
        print("Waiting for video feed...")
        if not stream.wait_for_first_frame(timeout=FIRST_FRAME_TIMEOUT_S):
            reason = stream.error or f"no frame received within {FIRST_FRAME_TIMEOUT_S:.0f}s"
            raise RuntimeError(f"Video source unavailable: {reason}")

        if stream.is_video_file:
            print("Video loaded! Detection starting...")
            print("  [Space] Pause/Resume  |  [Q] Quit")
        else:
            print("Camera feed active! Detection starting... Press 'q' to exit.")

    def _handle_key(self, key: int, raw) -> bool:
        # Act on a key pressed in the window (raw: the frame on screen). False when the user quits.
        stream = self.app.video_stream
        if self.calibration.is_active:
            self.calibration.handle_key(key)
        elif key == ord("q"):
            return False
        elif key == ord(" ") and stream.is_video_file:
            stream.playback.toggle_pause()
        elif key == ord("c") and raw is not None:
            self.calibration.begin(raw)
        return True

    def _run(self):
        app = self.app
        app.start_workers()
        stream = app.video_stream
        self._wait_for_video(stream)

        cv2.namedWindow(_WINDOW_TITLE, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(_WINDOW_TITLE, 1024, 576)
        cv2.setMouseCallback(_WINDOW_TITLE, self.calibration.on_mouse)

        frame_counter = 0
        last_shown = None   # what the last drawn frame showed, to skip redrawing an unchanged one
        window_shown = False
        while True:
            if not stream.is_running:
                raise RuntimeError(f"Video capture stopped: {stream.error or 'unknown error'}")

            raw = stream.frames.latest()
            shown = (raw, app.latest_detections, app.tracker.latest_mc_detections, stream.playback.is_paused())
            changed = last_shown is None or any(now is not before for now, before in zip(shown, last_shown))
            frame = None
            if raw is not None and (changed or self.calibration.is_active):
                last_shown = shown
                frame = self._draw()

            if frame is not None:
                frame_counter += 1
                if frame_counter % 300 == 0:
                    gc.collect()
                cv2.imshow(_WINDOW_TITLE, frame)
                window_shown = True

            key = cv2.waitKey(1 if frame is not None else 5) & 0xFF
            # Window closed with the X button: treat like [Q] instead of letting imshow reopen it
            if window_shown and cv2.getWindowProperty(_WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
                break
            if not self._handle_key(key, raw):
                break

    def _draw(self):
        if not self.calibration.is_active:
            return self.app.render_frame(show_progress=True)
        try:
            return self.calibration.draw(self.calibration.frame.copy())
        except Exception as e:
            log_error("Render", e)
            return self.calibration.frame
