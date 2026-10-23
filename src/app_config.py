from dataclasses import dataclass
import os

from dotenv import load_dotenv

import torch


load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    rtsp_url: str
    video_file: str
    
    model_path: str = "runs/detect/train-14/weights/best.pt"
    motorcycle_model_path: str = "yolov8n.pt"

    ###DEFAULT CONFIG VALUES###
    # Detection parameters
    detection_imgsz: int = 640
    detection_conf: float = 0.25
    
    # When to 'lock' an OCR result and print it: minimum confidence (0-1)
    ocr_lock_confidence: float = 0.90
    # Number of consecutive updates (stable reads) required to consider a result stable
    ocr_lock_stable_count: int = 2
    
    # Motorcycle tracking parameters
    # distance_threshold: max pixel distance between bbox centers to consider same motorcycle.
    # Increase if motorcycles move fast between frames (e.g. 250 for ~30fps, higher for low-fps cameras).
    motorcycle_track_distance: float = 250.0
    # max_age_s: how long (seconds) to keep a track alive without a new detection.
    # Increase if YOLO sometimes misses a motorcycle for multiple frames.
    motorcycle_track_max_age_s: float = 6.0
    
    # Display parameters
    display_fps: int = 30
    ocr_conf_threshold: float = 0.80
    ocr_cache_ttl_s: float = 5.0
    ocr_cache_distance: float = 120.0

    # Speed calibration: 4 ground points in the image (far-left;far-right;near-right;near-left)
    # and the real size "W,L" in meters of that rectangle (W = pts 1-2, L = pts 2-3).
    speed_calib_points: str = ""
    speed_calib_world: str = ""

    @classmethod
    def from_env(cls) -> "AppConfig":
        def _getenv(key, cast, default):
            v = os.getenv(key)
            if v is None:
                return default
            try:
                return cast(v)
            except Exception:
                return default

        return cls(
            rtsp_url=os.getenv("RTSP_URL", ""),
            video_file=os.getenv("VIDEO_FILE", ""),
            model_path=_getenv("MODEL_PATH", str, cls.model_path),
            motorcycle_model_path=_getenv("MOTORCYCLE_MODEL_PATH", str, cls.motorcycle_model_path),
            detection_imgsz=_getenv("DETECTION_IMGSZ", int, cls.detection_imgsz),
            detection_conf=_getenv("DETECTION_CONF", float, cls.detection_conf),
            ocr_lock_confidence=_getenv("OCR_LOCK_CONFIDENCE", float, cls.ocr_lock_confidence),
            ocr_lock_stable_count=_getenv("OCR_LOCK_STABLE_COUNT", int, cls.ocr_lock_stable_count),
            motorcycle_track_distance=_getenv("MOTORCYCLE_TRACK_DISTANCE", float, cls.motorcycle_track_distance),
            motorcycle_track_max_age_s=_getenv("MOTORCYCLE_TRACK_MAX_AGE_S", float, cls.motorcycle_track_max_age_s),
            display_fps=_getenv("DISPLAY_FPS", int, cls.display_fps),
            ocr_conf_threshold=_getenv("OCR_CONF_THRESHOLD", float, cls.ocr_conf_threshold),
            ocr_cache_ttl_s=_getenv("OCR_CACHE_TTL_S", float, cls.ocr_cache_ttl_s),
            ocr_cache_distance=_getenv("OCR_CACHE_DISTANCE", float, cls.ocr_cache_distance),
            speed_calib_points=os.getenv("SPEED_CALIB_POINTS", ""),
            speed_calib_world=os.getenv("SPEED_CALIB_WORLD", ""),
        )

    @property
    def inference_quantize(self) -> int:
        return 16 if torch.cuda.is_available() else 32


def configure_torch_runtime():
    use_cuda = torch.cuda.is_available()
    cuda_count = torch.cuda.device_count() if use_cuda else 0
    primary_device = "cuda:0" if use_cuda else "cpu"
    secondary_device = "cuda:1" if cuda_count > 1 else primary_device
    use_half = use_cuda

    if use_cuda:
        torch.backends.cudnn.benchmark = True
        try:
            torch.backends.cuda.matmul.allow_tf32 = True
        except Exception:
            pass
        try:
            torch.set_float32_matmul_precision("high")
        except Exception:
            pass
        try:
            # Leave headroom on the GPU for the separate OCR process (also GPU-based)
            torch.cuda.set_per_process_memory_fraction(0.7, device=0)
        except Exception:
            pass

    return primary_device, secondary_device, use_half


def load_yolo_model(model_path: str, device: str, use_half: bool):
    from ultralytics import YOLO

    model = YOLO(model_path)
    try:
        model.to(device)
    except Exception:
        pass

    if use_half:
        try:
            model.model.half()
        except Exception:
            pass

    return model