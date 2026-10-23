import multiprocessing as mp
import queue
import re
import traceback

import cv2
import numpy as np
import torch

from province import extract_thai_province
from utils import log_error


def validate_and_clean_thai_plate(raw_text, conf):
    if not raw_text or conf < 0.25:
        return "", 0.0

    # 1. Clean the text but KEEP spaces initially to help with ambiguity
    cleaned = re.sub(r"[^\u0E00-\u0E7F0-9\s]", "", raw_text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned) # normalize spaces
    
    if len(cleaned.replace(" ", "")) < 2:
        return "", 0.0

    # Format Regex components
    # Plate part: optional 1-9 followed by 1-3 Thai letters
    plate_regex = r"([1-9]?[\u0E00-\u0E7F]{1,3})"
    # Number part: 1 to 4 digits
    num_regex = r"([0-9]{1,4})"

    # 2. Check Standard Format (with or without space)
    std_match = re.match(f"^{plate_regex}\\s?{num_regex}$", cleaned)
    if std_match:
        return f"{std_match.group(1)} {std_match.group(2)}", conf

    # 3. Check Swapped Format (with or without space)
    swap_match = re.match(f"^{num_regex}\\s?{plate_regex}$", cleaned)
    if swap_match:
        return f"{swap_match.group(2)} {swap_match.group(1)}", conf

    # If it doesn't match either, it's invalid grammar -> reject
    return "", 0.0


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
    # Grow GPU memory on demand instead of Paddle's default upfront ~92% reservation,
    # so this process can share the GPU with the main (YOLO) process's CUDA context.
    os.environ.setdefault("FLAGS_allocator_strategy", "auto_growth")
    warnings.filterwarnings("ignore")

    MIN_CONF = 0.10
    # Skip the remaining preprocessing variants once plate and province both reach this confidence
    EARLY_STOP_CONF = 0.90

    print("[OCR-Process] Loading PaddleOCR ...")
    try:
        from paddleocr import PaddleOCR

        ocr_device = "gpu" if torch.cuda.is_available() else "cpu"
        ocr = PaddleOCR(lang="th", device=ocr_device, det_limit_side_len=640)
    except Exception as e:
        # Tell the main process why OCR is unavailable instead of dying silently
        traceback.print_exc()
        out_q.put({"error": f"PaddleOCR failed to load: {type(e).__name__}: {e}"})
        return
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
                # Check that the area is large enough, not small noise
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

    def _generate_ocr_variants(crop_bgr):
        crop_bgr = _apply_perspective_transform(crop_bgr)
        if crop_bgr is None or crop_bgr.size == 0:
            return []

        h, w = crop_bgr.shape[:2]

        max_side = 1600
        if max(h, w) > max_side:
            scale = max_side / float(max(h, w))
            crop_bgr = cv2.resize(crop_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            h, w = crop_bgr.shape[:2]

        if w < 10 or h < 10:
            return []

        target_w = 600
        scale = target_w / float(w)
        new_w = target_w
        new_h = int(h * scale)

        if new_h > 1000:
            scale = 1000.0 / float(h)
            new_w = int(w * scale)
            new_h = int(h * scale)

        crop_bgr = cv2.resize(crop_bgr, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

        MIN_OCR_H = 80
        if new_h < MIN_OCR_H:
            pad_total = MIN_OCR_H - new_h
            pad_top = pad_total // 2
            pad_bot = pad_total - pad_top
            crop_bgr = cv2.copyMakeBorder(
                crop_bgr, pad_top, pad_bot, 0, 0,
                cv2.BORDER_CONSTANT, value=(255, 255, 255)
            )

        # Base Denoise & Contrast
        base_img = cv2.bilateralFilter(crop_bgr, 11, 17, 17)
        base_img = cv2.convertScaleAbs(base_img, alpha=1.2, beta=10)
        base_rgb = cv2.cvtColor(base_img, cv2.COLOR_BGR2RGB)
        gray = cv2.cvtColor(base_img, cv2.COLOR_BGR2GRAY)

        variants = []
        
        # 1. Original (Base)
        variants.append(base_rgb)
        
        # 2. CLAHE
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        cl1 = clahe.apply(gray)
        variants.append(cv2.cvtColor(cl1, cv2.COLOR_GRAY2RGB))
        
        # 3. Adaptive Threshold
        th_adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        variants.append(cv2.cvtColor(th_adapt, cv2.COLOR_GRAY2RGB))
        
        # 4. Otsu Threshold
        _, th_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(cv2.cvtColor(th_otsu, cv2.COLOR_GRAY2RGB))
        
        # 5. Sharpen
        kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
        sharpened = cv2.filter2D(base_rgb, -1, kernel)
        variants.append(sharpened)
        
        # 6. Gamma Correction (gamma = 0.6)
        gamma = 0.6
        invGamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** invGamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
        gamma_img = cv2.LUT(base_rgb, table)
        variants.append(gamma_img)
        
        return variants

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
        except queue.Empty:
            continue
        except (EOFError, OSError):
            # The main process went away and closed the queue
            break

        if item is None:
            break

        frame_gen = 0
        frame_t = 0.0
        if isinstance(item, dict):
            frame_gen = item.get("gen", 0)
            frame_t = float(item.get("t", 0.0))
            item = item.get("items", [])

        det_results = []
        for item_data in item:
            try:
                if len(item_data) >= 5:
                    bbox, crop_bytes, shape, dtype_str, track_id = item_data[:5]
                else:
                    bbox, crop_bytes, shape, dtype_str = item_data[:4]
                    track_id = None
                crop_bgr = np.frombuffer(crop_bytes, dtype=np.dtype(dtype_str)).reshape(shape).copy()
                variants = _generate_ocr_variants(crop_bgr)
            except Exception as e:
                log_error("OCR-Process", e)
                continue

            best_plate_text, best_plate_conf = "", 0.0
            best_prov_text, best_prov_conf = "", 0.0

            for v_img in variants:
                try:
                    plate_out = ocr.ocr(v_img)
                except Exception as e:
                    # One bad variant should not cost the other variants of this plate
                    log_error("OCR-Process", e)
                    continue
                candidates = _extract_ocr_candidates(plate_out)
                
                v_plate_parts = []
                v_prov_text, v_prov_conf = "", 0.0

                for t, s in candidates:
                    t_clean = str(t).strip()
                    if not t_clean:
                        continue

                    detected_prov = extract_thai_province(t_clean)
                    if detected_prov:
                        if not v_prov_text:
                            v_prov_text, v_prov_conf = detected_prov, float(s)
                        continue

                    v_plate_parts.append((t_clean, float(s)))

                v_plate_text, v_plate_conf = "", 0.0
                if v_plate_parts:
                    combined = " ".join(t for t, _ in v_plate_parts if t)
                    avg_conf = sum(s for _, s in v_plate_parts) / len(v_plate_parts)
                    v_plate_text, v_plate_conf = validate_and_clean_thai_plate(combined, avg_conf)

                if v_plate_conf > best_plate_conf:
                    best_plate_conf = v_plate_conf
                    best_plate_text = v_plate_text
                
                if v_prov_conf > best_prov_conf:
                    best_prov_conf = v_prov_conf
                    best_prov_text = v_prov_text

                # Each variant is a full PaddleOCR pass; stop once both parts are read confidently
                if best_plate_conf >= EARLY_STOP_CONF and best_prov_conf >= EARLY_STOP_CONF:
                    break

            det_results.append((bbox, best_plate_text, best_plate_conf, best_prov_text, best_prov_conf, track_id))

        try:
            out_q.put({"gen": frame_gen, "t": frame_t, "results": det_results}, timeout=5.0)
        except queue.Full:
            pass  # main process is not consuming (busy or shutting down); drop this batch
        except (EOFError, OSError):
            break