import cv2
from .render import draw_text_on_cv_img


class CalibrationController:
    # Manages interactive ground-plane speed calibration via mouse clicks and keyboard.

    def __init__(self, set_calibration, on_pause=None, on_resume=None):
        self.set_calibration = set_calibration   # DetectionApp.set_calibration(points, (width_m, length_m))
        self.on_pause = on_pause
        self.on_resume = on_resume

        self.pts = None        # list of clicked points while calibrating, else None
        self.frame = None      # frozen frame shown during calibration
        self.w = None          # width (m) once entered
        self.input_text = ""   # digits typed in the window
        self.msg = ""          # error/info text shown in the window

    @property
    def is_active(self) -> bool:
        return self.pts is not None

    def begin(self, frame):
        self.frame = frame.copy()
        self.pts = []
        self.w = None
        self.input_text = ""
        self.msg = ""
        if self.on_pause:
            self.on_pause()
        print("[Speed] Click 4 ground points forming a rectangle on the road (e.g. lane markings):")
        print("        1 far-left, 2 far-right, 3 near-right, 4 near-left.  [Esc] cancel")

    def end(self):
        self.pts = None
        self.frame = None
        if self.on_resume:
            self.on_resume()

    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and self.pts is not None and len(self.pts) < 4:
            self.pts.append((x, y))

    def handle_key(self, key: int):
        if key == 27:  # Esc
            self.end()
            print("[Speed] Calibration cancelled.")
            return

        if self.pts is None or len(self.pts) < 4:
            return

        if key in (13, 10):  # Enter
            try:
                val = float(self.input_text)
                if val <= 0:
                    raise ValueError
            except ValueError:
                self.msg = "Invalid number, enter a value > 0"
                self.input_text = ""
                return
            self.input_text = ""
            self.msg = ""
            if self.w is None:
                self.w = val
            else:
                self._apply_calibration(self.w, val)
        elif key == 8:  # Backspace
            self.input_text = self.input_text[:-1]
        elif key != 255 and chr(key) in "0123456789." and len(self.input_text) < 8:
            self.input_text += chr(key)

    def _apply_calibration(self, w: float, l: float):
        pts = list(self.pts)
        try:
            self.set_calibration(pts, (w, l))
        except Exception as e:
            # Let the user re-click instead of leaving the window stuck
            self.pts = []
            self.w = None
            self.msg = f"Calibration failed: {e}. Click 4 points again"
            print(f"[Speed] Calibration failed: {e}")
            return
        print("[Speed] Calibrated.")
        self.end()

    def draw(self, frame):
        # Draw calibration points, lines, and instructions on the frame.
        pts = self.pts or []
        for i, p in enumerate(pts):
            cv2.circle(frame, p, 6, (0, 0, 255), -1)
            cv2.putText(frame, str(i + 1), (p[0] + 8, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2,
                        cv2.LINE_AA)
            if i > 0:
                cv2.line(frame, pts[i - 1], p, (0, 255, 255), 2, cv2.LINE_AA)
        if len(pts) == 4:
            cv2.line(frame, pts[3], pts[0], (0, 255, 255), 2, cv2.LINE_AA)
            what = "WIDTH (point 1->2)" if self.w is None else "LENGTH (point 2->3)"
            text = f"Real {what} in meters: {self.input_text}_  [Enter] ok  [Esc] cancel"
        else:
            text = f"CALIBRATION: click point {len(pts) + 1}/4  [Esc] cancel"

        frame = draw_text_on_cv_img(frame, text, (10, 96), font_size=22, text_color=(255, 255, 255),
                                    bg_color=(0, 0, 0), bg_alpha=0.7)
        if self.msg:
            frame = draw_text_on_cv_img(frame, self.msg, (10, 132), font_size=22,
                                        text_color=(255, 255, 255), bg_color=(0, 0, 0), bg_alpha=0.7)
        return frame
