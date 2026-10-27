import cv2
import numpy as np
from datetime import timedelta
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont


@lru_cache(maxsize=8)
def _load_font(font_size):
    for font_path in ["C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/tahomabd.ttf"]:
        try:
            return ImageFont.truetype(font_path, font_size)
        except Exception:
            pass
    return ImageFont.load_default()


_MEASURE = ImageDraw.Draw(Image.new("L", (1, 1)))


def draw_text_on_cv_img(img_cv, text, pos, font_size=24, text_color=(255, 255, 255), bg_color=(0, 180, 0), bg_alpha=1.0):
    """Draw a label in place and return img_cv. Only a small ROI goes through PIL, not the whole frame."""
    x, y = int(pos[0]), int(pos[1])
    font = _load_font(font_size)

    left, top, right, bottom = _MEASURE.textbbox((0, 0), text, font=font)
    w = right - left
    h = bottom - top

    padding = 4
    bg_box = [x, max(0, y - h - padding * 2), x + w + padding * 2, max(h + padding * 2, y)]

    margin = 10
    img_h, img_w = img_cv.shape[:2]
    rx0, ry0 = max(0, bg_box[0] - margin), max(0, bg_box[1] - margin)
    rx1, ry1 = min(img_w, bg_box[2] + margin), min(img_h, bg_box[3] + margin)
    if rx0 >= rx1 or ry0 >= ry1:
        return img_cv

    roi_pil = Image.fromarray(cv2.cvtColor(img_cv[ry0:ry1, rx0:rx1], cv2.COLOR_BGR2RGB))
    local_box = [bg_box[0] - rx0, bg_box[1] - ry0, bg_box[2] - rx0, bg_box[3] - ry0]

    if bg_alpha >= 1.0:
        ImageDraw.Draw(roi_pil).rectangle(local_box, fill=bg_color)
    else:
        overlay = Image.new("RGBA", roi_pil.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(local_box, fill=(*bg_color, int(255 * bg_alpha)))
        roi_pil = Image.alpha_composite(roi_pil.convert("RGBA"), overlay).convert("RGB")

    ImageDraw.Draw(roi_pil).text(
        (x + padding - rx0, max(0, y - h - padding) - ry0), text, font=font, fill=text_color
    )

    img_cv[ry0:ry1, rx0:rx1] = cv2.cvtColor(np.array(roi_pil), cv2.COLOR_RGB2BGR)
    return img_cv


def draw_video_overlay(frame, pos_ms: float, total_ms: float, proc_fps: float, paused: bool,
                       show_progress: bool = True):
    """Draw an overlay showing timestamp, progress bar, and FPS on the frame.
    The web dashboard has its own seek bar, so it turns the progress bar off."""
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

    if not show_progress:
        return frame

    # --- Progress bar (bottom) ---
    bar_h = 6
    bar_y = h - bar_h
    progress = pos_ms / total_ms if total_ms > 0 else 0
    filled_w = int(w * progress)

    cv2.rectangle(frame, (0, bar_y), (w, h), (60, 60, 60), -1)
    cv2.rectangle(frame, (0, bar_y), (filled_w, h), (0, 200, 120), -1)

    return frame


def draw_calibration(frame, points):
    """Outline the speed calibration rectangle (4 points), where it is on this frame."""
    pts = np.round(np.array(points)).astype(np.int32)
    cv2.polylines(frame, [pts], isClosed=True, color=(21, 204, 250), thickness=2, lineType=cv2.LINE_AA)
    for i, (x, y) in enumerate(pts):
        cv2.circle(frame, (int(x), int(y)), 7, (77, 72, 229), -1, cv2.LINE_AA)
        cv2.circle(frame, (int(x), int(y)), 7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(frame, str(i + 1), (int(x) + 9, int(y) - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (255, 255, 255), 2, cv2.LINE_AA)
    return frame


def draw_detections(frame, dets, mc_dets, mc_ids, speeds, helmets, riders=None, max_riders=2):
    """Draw motorcycle bounding boxes, speed, rider count, helmet status, and license plate detections."""
    riders = riders or {}
    for (mx1, my1, mx2, my2), m_conf in mc_dets:
        tid = mc_ids.get((mx1, my1, mx2, my2))
        status = helmets.get(tid)
        no_helmet = status is not None and status[1] > 0
        n_riders = riders.get(tid)
        overload = n_riders is not None and n_riders > max_riders
        violation = no_helmet or overload
        cv2.rectangle(frame, (mx1, my1), (mx2, my2), (0, 0, 255) if violation else (255, 100, 0), 2)
        label_mc = f"Motorcycle {m_conf * 100:.0f}%"
        spd = speeds.get(tid)
        if spd is not None:
            label_mc += f"  {spd:.0f} km/h"
        if n_riders:
            label_mc += f"  Riders: {n_riders}" + ("  OVERLOAD" if overload else "")
        if no_helmet:
            label_mc += f"  NO HELMET x{status[1]}"
        elif status is not None and status[0] > 0:
            label_mc += f"  Helmet x{status[0]}"
        frame = draw_text_on_cv_img(
            frame, label_mc, (mx1, max(30, my1)),
            font_size=22, text_color=(255, 255, 255), bg_color=(0, 0, 0), bg_alpha=0.7
        )

    for item in dets:
        (x1, y1, x2, y2), text, conf = item[:3]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)

        if text:
            label = f"{text} ({conf * 100:.0f}%)"
            frame = draw_text_on_cv_img(
                frame, label, (x1, y1),
                font_size=24, text_color=(255, 255, 255), bg_color=(0, 180, 0)
            )

    return frame
