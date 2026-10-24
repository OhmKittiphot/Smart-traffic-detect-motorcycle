from dataclasses import dataclass
import os

from dotenv import load_dotenv

import torch


load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    rtsp_url: str
    video_file: str

    motorcycle_model_path: str = "models/yolov8n.pt"
    # Single-class Thai motorcycle plate model (train.py plate)
    model_path: str = "runs/detect/moto-plate/weights/best.pt"
    helmet_model_path: str = "runs/detect/helmet-rider/weights/best.pt"

    ###DEFAULT CONFIG VALUES###
    # Detection parameters
    detection_imgsz: int = 640
    # Plates are small in wide shots; the plate model was trained at 960
    plate_imgsz: int = 960
    # Where to look for plates: "roi" = only inside each detected motorcycle, cropped from the full-size
    # frame (no plate search at all when no motorcycle is close enough); "frame" = the whole frame at
    # plate_imgsz (also used when the motorcycle model is missing). On plate-dataset/test "frame" found
    # 71% of plates and "roi" 54%, because the motorcycle model misses some bikes (67% with
    # detection_imgsz 960). "roi" gains most once the plate model is trained on motorcycle crops.
    plate_search: str = "roi"
    # Input size for each motorcycle region. Measured on plate-dataset/test: 320 found the most plates;
    # larger sizes enlarge the plate past what the current plate model was trained on
    plate_roi_imgsz: int = 320
    # Motorcycles shorter than this (pixels) are too far away for a readable plate and are skipped
    plate_min_bike_h: int = 40
    detection_conf: float = 0.25
    helmet_conf: float = 0.25
    # Lower threshold for counting riders only: a passenger's head behind the driver often scores
    # 0.25-0.4, while single-rider bikes show no extra heads even far below this.
    # Helmet / NO HELMET status still uses helmet_conf.
    rider_conf: float = 0.20
    # Heads are small in wide shots, so the helmet model runs at a higher resolution
    helmet_imgsz: int = 960
    # Class names of the helmet model (lowercase, "_" and "-" read as spaces); match your data.yaml
    helmet_class_names: tuple = ("helmet", "with helmet")
    no_helmet_class_names: tuple = ("head", "no helmet", "without helmet", "bare head")
    # Riders allowed on one motorcycle (Thai law: driver + 1 passenger); more is flagged OVERLOAD
    max_riders: int = 2
    # Median speed above this is reported as SPEEDING (only when speed is calibrated); 0 disables it
    speed_limit_kmh: float = 80.0
    # A motorcycle's summary is printed when it leaves; tracks seen in fewer YOLO frames are ignored
    report_min_frames: int = 5
    # When to 'lock' an OCR result and print it: minimum confidence (0-1)
    ocr_lock_confidence: float = 0.90
    # Number of consecutive updates (stable reads) required to consider a result stable
    ocr_lock_stable_count: int = 3
    
    # Motorcycle tracking parameters
    # distance_threshold: max pixel distance between bbox centers to consider same motorcycle.
    # Increase if motorcycles move fast between frames (e.g. 250 for ~30fps, higher for low-fps cameras).
    motorcycle_track_distance: float = 250.0
    # max_age_s: how long (seconds) to keep a track alive without a new detection.
    # Increase if YOLO sometimes misses a motorcycle for multiple frames.
    motorcycle_track_max_age_s: float = 6.0
    
    # Display parameters
    display_fps: int = 25
    ocr_conf_threshold: float = 0.40
    ocr_cache_ttl_s: float = 5.0
    ocr_cache_distance: float = 120.0

    # Speed calibration: 4 ground points in the image (far-left;far-right;near-right;near-left)
    # and the real size "W,L" in meters of that rectangle (W = pts 1-2, L = pts 2-3).
    speed_calib_points: str = ""
    speed_calib_world: str = ""

    @classmethod
    def from_env(cls) -> "AppConfig":
        # Only the video source and speed calibration come from .env; everything else is set above.
        return cls(
            rtsp_url=os.getenv("RTSP_URL", ""),
            video_file=os.getenv("VIDEO_FILE", ""),
            speed_calib_points=os.getenv("SPEED_CALIB_POINTS", ""),
            speed_calib_world=os.getenv("SPEED_CALIB_WORLD", ""),
        )

    @property
    def inference_quantize(self) -> int:
        return 16 if torch.cuda.is_available() else 32


def configure_torch_runtime():
    use_cuda = torch.cuda.is_available()
    primary_device = "cuda:0" if use_cuda else "cpu"
    use_half = use_cuda

    if use_cuda:
        # Off on purpose: helmet crops change shape every frame, and benchmark mode re-tunes for each new
        # shape (measured: 19 stalls of 0.7-1.2 s in 8 s of video vs none without it, same median speed)
        torch.backends.cudnn.benchmark = False
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

    return primary_device, use_half


def load_yolo_model(model_path: str, device: str, use_half: bool):
    from ultralytics import YOLO

    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"Model weights not found: {model_path}")

    model = YOLO(model_path)
    try:
        model.to(device)
    except Exception as e:
        print(f"[Model] Could not move {model_path} to {device} ({e}); predict() will place it per call.")

    if use_half:
        try:
            model.model.half()
        except Exception as e:
            print(f"[Model] FP16 not applied to {model_path} ({e}); running in FP32.")

    return model