from dataclasses import dataclass
import os

from dotenv import load_dotenv

import torch


load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    camera_url: str
    
    model_path: str = "runs/detect/train-14/weights/best.pt"
    motorcycle_model_path: str = "yolov8n.pt"
    
    # Detection parameters
    detection_imgsz: int = 640
    detection_conf: float = 0.25
    yolo_every_n: int = 2
    
    # OCR parameters
    ocr_cooldown_s: float = 0.2
    # When to 'lock' an OCR result and print it: minimum confidence (0-1)
    ocr_lock_confidence: float = 0.90
    # Number of consecutive updates (stable reads) required to consider a result stable
    ocr_lock_stable_count: int = 2
    # Minimum seconds between printed locked results
    ocr_lock_print_cooldown_s: float = 8.0
    
    # Display parameters
    display_fps: int = 25
    ocr_conf_threshold: float = 0.80
    ocr_cache_ttl_s: float = 5.0
    ocr_cache_distance: float = 120.0

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
            camera_url=os.getenv("CAMERA_URL"),
            model_path=os.getenv("MODEL_PATH", cls.model_path),
            motorcycle_model_path=os.getenv("MOTORCYCLE_MODEL_PATH", cls.motorcycle_model_path),
            detection_imgsz=_getenv("DETECTION_IMGSZ", int, cls.detection_imgsz),
            detection_conf=_getenv("DETECTION_CONF", float, cls.detection_conf),
            yolo_every_n=_getenv("YOLO_EVERY_N", int, cls.yolo_every_n),
            ocr_cooldown_s=_getenv("OCR_COOLDOWN_S", float, cls.ocr_cooldown_s),
            ocr_lock_confidence=_getenv("OCR_LOCK_CONFIDENCE", float, cls.ocr_lock_confidence),
            ocr_lock_stable_count=_getenv("OCR_LOCK_STABLE_COUNT", int, cls.ocr_lock_stable_count),
            ocr_lock_print_cooldown_s=_getenv("OCR_LOCK_PRINT_COOLDOWN_S", float, cls.ocr_lock_print_cooldown_s),
            display_fps=_getenv("DISPLAY_FPS", int, cls.display_fps),
            ocr_conf_threshold=_getenv("OCR_CONF_THRESHOLD", float, cls.ocr_conf_threshold),
            ocr_cache_ttl_s=_getenv("OCR_CACHE_TTL_S", float, cls.ocr_cache_ttl_s),
            ocr_cache_distance=_getenv("OCR_CACHE_DISTANCE", float, cls.ocr_cache_distance),
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