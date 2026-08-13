import os
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
os.environ["FLAGS_minloglevel"] = "2"
os.environ["GLOG_minloglevel"] = "2"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|"
    "analyzeduration;0|probesize;32"
)

import multiprocessing as mp
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

from app_config import AppConfig, configure_torch_runtime
from detection_app import DetectionApp

if __name__ == "__main__":
    mp.freeze_support()

    config = AppConfig.from_env()
    device, device_secondary, use_half = configure_torch_runtime()

    app = DetectionApp(config, device, device_secondary, use_half)
    app.start()
