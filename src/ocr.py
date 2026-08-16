import multiprocessing as mp
import re

import cv2
import numpy as np
import torch

from province import extract_thai_province


def validate_and_clean_thai_plate(raw_text, conf):
    if not raw_text or conf < 0.25:
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

    if len(cleaned_text.replace(" ", "")) < 2:
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

    def _apply_perspective_transform(image):
        if image is None or image.size == 0:
            return image
            
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.bilateralFilter(gray, 11, 17, 17)
        edged = cv2.Canny(blur, 30, 200)
        
        contours, _ = cv2.findContours(edged.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return image
            
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]
        
        plate_contour = None
        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4:
                # ตรวจสอบว่าพื้นที่ใหญ่พอ ไม่ใช่ noise เล็กๆ
                if cv2.contourArea(approx) > (image.shape[0]*image.shape[1] * 0.3):
                    plate_contour = approx
                    break
                
        if plate_contour is None:
            return image
            
        pts = plate_contour.reshape(4, 2)
        # Order points: tl, tr, br, bl
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        
        (tl, tr, br, bl) = rect
        widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        maxWidth = max(int(widthA), int(widthB))
        
        heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        maxHeight = max(int(heightA), int(heightB))
        
        if maxWidth == 0 or maxHeight == 0:
            return image
            
        dst = np.array([
            [0, 0],
            [maxWidth - 1, 0],
            [maxWidth - 1, maxHeight - 1],
            [0, maxHeight - 1]], dtype="float32")
            
        M = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))
        return warped

    def _preprocess_crop_for_ocr(crop_bgr):
        crop_bgr = _apply_perspective_transform(crop_bgr)
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

        # เพิ่ม padding แนวตั้งให้สูงอย่างน้อย 80px
        # ป้องกัน PaddleOCR ขยาย short-side → 640 แล้วด้าน wide พุ่งเกิน 4000px
        MIN_OCR_H = 80
        if new_h < MIN_OCR_H:
            pad_total = MIN_OCR_H - new_h
            pad_top = pad_total // 2
            pad_bot = pad_total - pad_top
            crop_bgr = cv2.copyMakeBorder(
                crop_bgr, pad_top, pad_bot, 0, 0,
                cv2.BORDER_CONSTANT, value=(255, 255, 255)
            )

        # CLAHE: เพิ่ม contrast ท้องถิ่นช่วยอ่านป้ายที่ไม่ชัด
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        crop_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        crop_bgr = cv2.convertScaleAbs(crop_bgr, alpha=1.2, beta=10)
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

    def _parse_digits_only(ocr_out):
        if not ocr_out:
            return "", 0.0
        text, conf = _pick_best_text(ocr_out, min_conf=MIN_CONF)
        if not text:
            return "", 0.0
        digits_only = re.sub(r"[^0-9]", "", text)
        return (digits_only, conf) if digits_only else ("", 0.0)

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

            plate_text, plate_conf = "", 0.0
            province_text, province_conf = "", 0.0

            plate_out = ocr.ocr(plate_crop)
            candidates = _extract_ocr_candidates(plate_out)
            plate_parts = []

            for t, s in candidates:
                t_clean = str(t).strip()
                if not t_clean:
                    continue

                detected_prov = extract_thai_province(t_clean)
                if detected_prov:
                    if not province_text:
                        province_text, province_conf = detected_prov, float(s)
                    continue

                plate_parts.append((t_clean, float(s)))

            if plate_parts:
                combined = " ".join(t for t, _ in plate_parts if t)
                avg_conf = sum(s for _, s in plate_parts) / len(plate_parts)
                plate_text, plate_conf = validate_and_clean_thai_plate(combined, avg_conf)

            det_results.append((bbox, plate_text, plate_conf, province_text, province_conf))

        out_q.put(det_results)