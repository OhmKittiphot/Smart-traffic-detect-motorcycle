import multiprocessing as mp
import re

import cv2
import numpy as np
import torch

from province import extract_thai_province


def validate_and_clean_thai_plate(raw_text, conf):
    if not raw_text or conf < 0.50:
        return "", 0.0

    text = raw_text.strip()

    english_chars = re.findall(r"[A-Za-z]", text)
    if len(english_chars) > 2:
        return "", 0.0

    thai_letters = "".join(re.findall(r"[\u0E00-\u0E7F]", text))
    digits = "".join(re.findall(r"[0-9]", text))
    if not thai_letters and not digits:
        return "", 0.0

    cleaned_text = re.sub(r"[^\u0E00-\u0E7F0-9\s]", "", text).strip()
    cleaned_text = re.sub(r"\s+", " ", cleaned_text)

    if len(cleaned_text.replace(" ", "")) < 3:
        return "", 0.0

    return cleaned_text, conf


def _extract_ocr_candidates(ocr_out):
    if not ocr_out:
        return []

    candidates = []

    def _push(text, score):
        text = str(text).strip()
        if text:
            candidates.append((text, score))

    def _extract(data):
        if isinstance(data, dict):
            rec_texts = data.get("rec_texts") or data.get("texts") or []
            rec_scores = data.get("rec_scores") or data.get("scores") or []
            for t, s in zip(rec_texts, rec_scores):
                try:
                    sc = float(s)
                except Exception:
                    sc = 0.0
                _push(t, sc)
        elif isinstance(data, (list, tuple)):
            for item in data:
                if isinstance(item, dict):
                    _extract(item)
                elif isinstance(item, (list, tuple)):
                    if len(item) == 2 and isinstance(item[0], str):
                        try:
                            sc = float(item[1])
                        except Exception:
                            sc = 0.0
                        _push(item[0], sc)
                    elif len(item) == 2 and isinstance(item[1], (list, tuple)) and len(item[1]) >= 2:
                        info = item[1]
                        try:
                            sc = float(info[1])
                        except Exception:
                            sc = 0.0
                        _push(info[0], sc)
                    else:
                        _extract(item)

    _extract(ocr_out)
    return candidates


def _pick_best_text(ocr_out, min_conf: float = 0.10):
    candidates = _extract_ocr_candidates(ocr_out)
    if not candidates:
        return "", 0.0

    valid = [(t, s) for t, s in candidates if s >= min_conf]
    if not valid:
        return max(candidates, key=lambda x: x[1])

    combined_text = " ".join(t for t, _ in valid)
    avg_conf = sum(s for _, s in valid) / len(valid)
    return combined_text, avg_conf


def _extract_plate_result(ocr_out):
    plate_text, plate_conf = _pick_best_text(ocr_out)
    return validate_and_clean_thai_plate(plate_text, plate_conf)


def _extract_province_result(ocr_out):
    province_text, province_conf = _pick_best_text(ocr_out)
    province = extract_thai_province(province_text)
    if province:
        return province, province_conf
    return "", 0.0


def ocr_worker(in_q: mp.Queue, out_q: mp.Queue):
    import os
    import warnings

    os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
    os.environ["FLAGS_minloglevel"] = "2"
    os.environ["GLOG_minloglevel"] = "2"
    warnings.filterwarnings("ignore")

    from paddleocr import PaddleOCR

    MIN_CONF = 0.10

    print("[OCR-Process] Loading PaddleOCR ...")
    ocr_device = "gpu" if torch.cuda.is_available() else "cpu"
    ocr = PaddleOCR(lang="th", device=ocr_device, det_limit_side_len=640)
    out_q.put("ready")
    print("[OCR-Process] PaddleOCR is ready!")

    def _preprocess_crop_for_ocr(crop_bgr):
        if crop_bgr is None or crop_bgr.size == 0:
            return crop_bgr

        h, w = crop_bgr.shape[:2]

        max_side = 1600
        if max(h, w) > max_side:
            scale = max_side / float(max(h, w))
            crop_bgr = cv2.resize(crop_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            h, w = crop_bgr.shape[:2]

        if w < 10 or h < 10:
            return None

        target_w = 600
        scale = target_w / float(w)
        new_w = target_w
        new_h = int(h * scale)

        if new_h > 1000:
            scale = 1000.0 / float(h)
            new_w = int(w * scale)
            new_h = int(h * scale)

        crop_bgr = cv2.resize(crop_bgr, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        crop_bgr = cv2.convertScaleAbs(crop_bgr, alpha=1.2, beta=0)
        crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        return crop_rgb

    def _extract_region(crop_bgr, top_ratio: float = 0.0, bottom_ratio: float = 1.0):
        if crop_bgr is None or crop_bgr.size == 0:
            return crop_bgr

        h, w = crop_bgr.shape[:2]
        top = max(0, min(h, int(h * top_ratio)))
        bottom = max(top + 1, min(h, int(h * bottom_ratio)))
        region = crop_bgr[top:bottom, :]
        return region if region.size > 0 else crop_bgr

    def _parse_plate_result(ocr_out):
        if not ocr_out:
            return "", 0.0

        plate_text, plate_conf = _pick_best_text(ocr_out, min_conf=MIN_CONF)
        return _extract_plate_result([(plate_text, plate_conf)])

    def _parse_province_result(ocr_out):
        if not ocr_out:
            return "", 0.0

        return _extract_province_result(ocr_out)

    while True:
        try:
            item = in_q.get(timeout=30.0)
        except Exception:
            continue

        if item is None:
            break

        det_results = []
        for bbox, crop_bytes, shape, dtype_str in item:
            crop_bgr = np.frombuffer(crop_bytes, dtype=np.dtype(dtype_str)).reshape(shape).copy()
            plate_crop = _preprocess_crop_for_ocr(crop_bgr)
            province_crop = _preprocess_crop_for_ocr(_extract_region(crop_bgr, top_ratio=0.42, bottom_ratio=1.0))

            plate_text, plate_conf = "", 0.0
            province_text, province_conf = "", 0.0

            if plate_crop is not None:
                try:
                    plate_out = ocr.ocr(plate_crop)
                    plate_text, plate_conf = _parse_plate_result(plate_out)
                except Exception as e:
                    print(f"[OCR-Process] Plate OCR Error: {e}")

            if province_crop is not None:
                try:
                    province_out = ocr.ocr(province_crop)
                    province_text, province_conf = _parse_province_result(province_out)
                except Exception as e:
                    print(f"[OCR-Process] Province OCR Error: {e}")

            det_results.append((bbox, plate_text, plate_conf, province_text, province_conf))

        out_q.put(det_results)