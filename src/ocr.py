import multiprocessing as mp
import os
import queue
import re

import cv2
import numpy as np
import torch

from ocr_voting import UNKNOWN
from province import extract_thai_province
from utils import log, log_error, setup_console_logging

# A Thai motorcycle plate has three lines: the series (e.g. '6ขน'), the province and the number (e.g. '690').
# Each line is cut from the plate crop by a fixed band of its height (the crop carries 10% padding around
# the YOLO box) and read with text recognition alone. Whole-plate OCR first has to find the lines itself,
# and on plates ~40 px tall it often missed or merged them: on input/IMG_9122.MOV it read the number right
# on 28% of crops, against 59% when each line is read on its own (and at ~12 instead of ~1000 ms a plate)
LINE_BANDS = {"series": (0.05, 0.42), "province": (0.35, 0.65), "number": (0.55, 0.95)}
# Fraction of the width cut off each side of the series and number lines, where the plate frame,
# screws and the padding around the plate were read as extra characters
LINE_SIDE_TRIM = 0.12
REC_MODEL = "th_PP-OCRv5_mobile_rec"   # the recognition model PaddleOCR(lang="th") uses

SERIES_RE = re.compile(r"[1-9][ก-ฮ]{2}|[ก-ฮ]{2,3}")
NUMBER_RE = re.compile(r"[1-9][0-9]{0,3}")

# Blurred Thai characters often come out as Latin look-alikes ('6กจ' read as 'onจ', '5กฉ' as 'sna')
_THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙"
SERIES_LOOKALIKES = str.maketrans({"s": "5", "S": "5", "i": "1", "I": "1", "l": "1",
                                   **{d: str(i) for i, d in enumerate(_THAI_DIGITS)}})
NUMBER_LOOKALIKES = str.maketrans("OoDQIilSsBZz" + _THAI_DIGITS, "000011155822" + "0123456789")


def normalize_line(text, lookalikes, valid_char):
    """Map look-alike characters, drop what a plate never has (vowels, tone marks, punctuation)
    and mark any other invalid character as UNKNOWN."""
    text = re.sub(r"[ะ-๏]|[^\w]|_", "", str(text).translate(lookalikes))
    return "".join(ch if re.fullmatch(valid_char, ch) else UNKNOWN for ch in text)


def normalize_series(text):
    series = normalize_line(text, SERIES_LOOKALIKES, r"[0-9ก-ฮn]")
    # 'n' read as the second character was ก ('5กม' read as '5nม'); as the last one it was as often
    # ท or ด, so it is only mapped there
    if series[1:2] == "n":
        series = series[0] + "ก" + series[2:]
    return series.replace("n", UNKNOWN)


def normalize_number(text):
    return normalize_line(text, NUMBER_LOOKALIKES, r"[0-9]")


def is_valid_series(text):
    return bool(SERIES_RE.fullmatch(text))


def is_valid_number(text):
    return bool(NUMBER_RE.fullmatch(text))


def plate_sharpness(crop_bgr):
    """How much detail the plate shows: variance of the Laplacian at a fixed size, so a small,
    upscaled or motion-blurred plate scores low. Used to weight reads of one plate against each other."""
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (96, 64), interpolation=cv2.INTER_AREA if gray.shape[0] > 64 else cv2.INTER_CUBIC)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def cut_line(crop_bgr, band, side_trim=0.0, height=96):
    """The band of the plate holding one line, enlarged (the recognizer read these small lines
    better from 96 px than from its own 48 px input height)."""
    h, w = crop_bgr.shape[:2]
    top, bottom = int(h * band[0]), max(int(h * band[1]), int(h * band[0]) + 1)
    left, right = int(w * side_trim), max(int(w * (1 - side_trim)), int(w * side_trim) + 1)
    line = crop_bgr[top:bottom, left:right]
    scale = height / line.shape[0]
    return cv2.resize(line, (max(1, int(line.shape[1] * scale)), height), interpolation=cv2.INTER_CUBIC)


def read_plate(rec, crop_bgr):
    """Read the three lines of one plate crop with a PaddleOCR TextRecognition model.
    Returns {line: (text, score)}; series and number are normalized and may hold UNKNOWN."""
    lines = [cut_line(crop_bgr, band, 0.0 if name == "province" else LINE_SIDE_TRIM)
             for name, band in LINE_BANDS.items()]
    outputs = rec.predict(lines, batch_size=len(lines))
    read = {name: (out["rec_text"], float(out["rec_score"])) for name, out in zip(LINE_BANDS, outputs)}

    series, series_score = read["series"]
    number, number_score = read["number"]
    province_text, province_score = read["province"]
    province = extract_thai_province(province_text)
    return {
        "series": (normalize_series(series), series_score),
        "number": (normalize_number(number), number_score),
        "province": (province, province_score if province else 0.0),
    }


def load_recognizer(model_dir=None, device="gpu"):
    """The plate recognizer: the fine-tuned model in model_dir when it exists, else PaddleOCR's stock one."""
    from paddleocr import TextRecognition

    if model_dir and os.path.isdir(model_dir):
        log.info("[OCR-Process] Plate recognizer: %s", model_dir)
        return TextRecognition(model_name=REC_MODEL, model_dir=model_dir, device=device)
    log.info("[OCR-Process] Plate recognizer: stock %s (no fine-tuned model at '%s')", REC_MODEL, model_dir)
    return TextRecognition(model_name=REC_MODEL, device=device)


def ocr_worker(in_q: mp.Queue, out_q: mp.Queue, model_dir: str = ""):
    import warnings

    os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
    os.environ["FLAGS_minloglevel"] = "2"
    os.environ["GLOG_minloglevel"] = "2"
    # Grow GPU memory on demand instead of Paddle's default upfront ~92% reservation,
    # so this process can share the GPU with the main (YOLO) process's CUDA context.
    os.environ.setdefault("FLAGS_allocator_strategy", "auto_growth")
    warnings.filterwarnings("ignore")

    # A process of its own: the main process's logging set-up is not inherited
    setup_console_logging()
    log.info("[OCR-Process] Loading PaddleOCR ...")
    try:
        rec = load_recognizer(model_dir, "gpu" if torch.cuda.is_available() else "cpu")
    except Exception as e:
        # Tell the main process why OCR is unavailable instead of dying silently
        log.exception("[OCR-Process] PaddleOCR failed to load")
        out_q.put({"error": f"PaddleOCR failed to load: {type(e).__name__}: {e}"})
        return
    out_q.put("ready")
    log.info("[OCR-Process] PaddleOCR is ready!")

    while True:
        try:
            item = in_q.get(timeout=30.0)
        except queue.Empty:
            continue
        except (EOFError, OSError):
            # The main process went away and closed the queue
            break

        if item is None:
            break

        results = []
        for bbox, crop_bytes, shape, dtype_str, track_id in item["items"]:
            try:
                crop_bgr = np.frombuffer(crop_bytes, dtype=np.dtype(dtype_str)).reshape(shape)
                if min(crop_bgr.shape[:2]) < 10:
                    continue
                read = read_plate(rec, crop_bgr)
                read.update(bbox=bbox, track_id=track_id, sharpness=plate_sharpness(crop_bgr))
                results.append(read)
            except Exception as e:
                log_error("OCR-Process", e)

        try:
            out_q.put({"gen": item["gen"], "t": item["t"], "results": results}, timeout=5.0)
        except queue.Full:
            pass  # main process is not consuming (busy or shutting down); drop this batch
        except (EOFError, OSError):
            break
