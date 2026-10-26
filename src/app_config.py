from dataclasses import dataclass
import os

from dotenv import load_dotenv

import torch


load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    rtsp_url: str
    video_file: str

    # Motorcycle detector: the stock COCO model (its class "motorcycle" is picked by name); it finds
    # motorcycles on the wide, low-resolution iTIC CCTV views where models/motorcycle_det.pt found none
    motorcycle_model_path: str = "models/yolo26n.pt"
    # Thai motorcycle plate model
    plate_model_path: str = "models/plate_det_th.pt"
    helmet_model_path: str = "models/helmet_det.pt"
    # Rider count / helmet classifier. When the file exists it
    # replaces the helmet model above for rider counting and helmet status
    rider_model_path: str = "models/rider_cls.pt"
    # Fine-tuned plate text recognizer; PaddleOCR's stock Thai recognizer
    # is used when this folder does not exist
    plate_ocr_model_path: str = "models/plate_ocr_th"
    # Frames of classifier output averaged per motorcycle before its result is used
    rider_vote_window: int = 15
    rider_vote_min_frames: int = 3

    # Detection parameters
    detection_imgsz: int = 640
    # Whole-frame plate search size ("frame" mode); the plate model was trained at 640 on plate close-ups
    plate_imgsz: int = 640
    # Where to look for plates: "roi" = only inside each detected motorcycle, cropped from the full-size
    # frame (no plate search at all when no motorcycle is close enough); "frame" = the whole frame at
    # plate_imgsz (also used when the motorcycle model is missing). "roi" finds far more plates, and faster,
    # because a plate is only ~30 px in a 1080p frame
    plate_search: str = "roi"
    # Input size for each motorcycle region: 640 enlarges the plate to the size the model was trained on
    # (138 plates on the video vs 43 at 320)
    plate_roi_imgsz: int = 640
    # Fraction of the motorcycle box width/height added on each side of its plate search region.
    # 0 = the box itself: on the video it found 138 plates vs 102 with (0.2, 0.1), whose larger region
    # shrinks the plate
    plate_roi_pad: tuple = (0.0, 0.0)
    # Motorcycles shorter than this (pixels) are too far away for a readable plate and are skipped.
    # On the 1080p video, OCR read nothing useful from bikes under ~220 px; skipping them cut plate search
    # from 54 to 17 ms on frames with 5+ bikes (where playback lagged) and kept 93 of 95 plates >= 25 px tall
    plate_min_bike_h: int = 200
    # Plate boxes taller than this fraction of their motorcycle are dropped. On the video real plates
    # were 0.09-0.17 of the bike height; boxes around a whole bike, wheel or rider were 0.23-0.48
    plate_max_bike_h_ratio: float = 0.2
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
    # A track's plate is 'locked' (no more OCR) once its voted series and number came out the same
    # on this many consecutive reads. On input/IMG_9122.MOV 3 locked a wrong series early; 5 did not
    ocr_lock_stable_count: int = 5
    # Minimum time between OCR requests for one motorcycle; each read is one more vote for its plate
    ocr_request_interval_s: float = 0.1
    
    # Motorcycle tracking parameters
    # distance_threshold: max pixel distance between bbox centers to consider same motorcycle.
    # Increase if motorcycles move fast between frames (e.g. 250 for ~30fps, higher for low-fps cameras).
    motorcycle_track_distance: float = 250.0
    # max_age_s: how long (seconds) to keep a track alive without a new detection.
    # A bike is reported once it has been gone this long; 2.5 keeps the report within 3s of leaving the frame.
    # Increase if YOLO sometimes misses a motorcycle for multiple frames.
    motorcycle_track_max_age_s: float = 2.5
    
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
        # Only the video source comes from .env; everything else is set above.
        return cls(
            rtsp_url=os.getenv("RTSP_URL", ""),
            video_file=os.getenv("VIDEO_FILE", ""),
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