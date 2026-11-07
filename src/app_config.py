from dataclasses import dataclass
import os
import torch

from dotenv import load_dotenv
from .cuda_graphs import graph_yolo
from .utils import log

load_dotenv()

@dataclass(frozen=True)
class AppConfig:
    # Camera stream address (RTSP_URL in .env)
    rtsp_url: str
    # Video file to play (VIDEO_FILE in .env)
    video_file: str

    # Model files
    # Motorcycle detector
    motorcycle_model_path: str = "models/yolo26n.pt"
    # Helmet and bare head detector
    helmet_model_path: str = "models/helmet_det.pt"
    # Rider classifier
    rider_model_path: str = "models/rider_cls.pt"
    # Licence plate detector
    plate_model_path: str = "models/plate_det_th.pt"
    # Head classifier
    head_classifier_path: str = "models/head_cls.pt"
    # Plate OCR model folder
    plate_ocr_model_path: str = "models/plate_ocr_th"

    # Rider classifier
    # Number of recent frames averaged per motorcycle
    rider_vote_window: int = 15
    # Frames needed before the result is used
    rider_vote_min_frames: int = 3
    # Extra space above the motorcycle box in the crop, as a fraction of its height
    rider_crop_pad_up: float = 0.5
    # Probability at which a seat counts as taken
    rider_seat_threshold: float = 0.35
    # Smallest motorcycle crop height to classify (pixels)
    rider_min_crop_h: int = 64

    # Detection
    # Motorcycle model input size
    detection_imgsz: int = 640
    # Plate model input size when searching the whole frame
    plate_imgsz: int = 640
    # Where to search for plates: "roi" = inside each motorcycle, "frame" = the whole frame
    plate_search: str = "roi"
    # Plate model input size for each motorcycle region
    plate_roi_imgsz: int = 640
    # Extra space around the motorcycle box in the plate search, as a fraction of its (width, height)
    plate_roi_pad: tuple = (0.0, 0.0)
    # Smallest motorcycle height to search for a plate (pixels)
    plate_min_bike_h: int = 120
    # Largest plate height, as a fraction of the motorcycle height
    plate_max_bike_h_ratio: float = 0.25
    # Motorcycle detector confidence threshold
    detection_conf: float = 0.25
    # Plate detector confidence threshold
    plate_conf: float = 0.15
    # Helmet detector confidence threshold
    helmet_conf: float = 0.25
    # Confidence threshold for counting a head as a rider
    rider_conf: float = 0.20
    # Helmet model input size
    helmet_imgsz: int = 960
    # Helmet model classes that mean a helmet is worn
    helmet_class_names: tuple = ("helmet", "with helmet")
    # Helmet model classes that mean no helmet is worn
    no_helmet_class_names: tuple = ("head", "no helmet", "without helmet", "bare head")

    # Violations and reports
    # Most riders allowed on one motorcycle
    max_riders: int = 2
    # Speed limit in km/h (0 = off)
    speed_limit_kmh: float = 80.0
    # Fewest frames a motorcycle needs to be reported
    report_min_frames: int = 5

    # OCR requests
    # Identical reads in a row needed to lock a plate
    ocr_lock_stable_count: int = 5
    # Shortest time between OCR requests for one motorcycle (seconds)
    ocr_request_interval_s: float = 0.1

    # Motorcycle tracking
    # Largest distance between box centers to count as the same motorcycle (pixels)
    motorcycle_track_distance: float = 250.0
    # How long a track stays alive without a new detection (seconds)
    motorcycle_track_max_age_s: float = 2.5

    # Frame rate used when the video does not report one
    display_fps: int = 25

    # OCR results
    # Lowest OCR confidence a plate is kept with
    ocr_conf_threshold: float = 0.20
    # How long OCR reads are remembered (seconds)
    ocr_cache_ttl_s: float = 5.0
    # Largest distance to reuse a remembered read for a box without a track (pixels)
    ocr_cache_distance: float = 120.0

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            rtsp_url=os.getenv("RTSP_URL", ""),
            video_file=os.getenv("VIDEO_FILE", ""),
        )

    @property
    def inference_quantize(self):
        return 16 if torch.cuda.is_available() else 32


def configure_torch_runtime():
    use_cuda = torch.cuda.is_available()
    primary_device = "cuda:0" if use_cuda else "cpu"
    use_half = use_cuda

    if use_cuda:
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
        log.warning("[Model] Could not move %s to %s (%s); predict() will place it per call.", model_path, device, e)

    if use_half:
        try:
            model.model.half()
        except Exception as e:
            log.warning("[Model] FP16 not applied to %s (%s); running in FP32.", model_path, e)

    if device.startswith("cuda"):
        graph_yolo(model)   # several times faster on this project's GPU (see cuda_graphs.py)

    return model