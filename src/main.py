import os
import sys

import multiprocessing as mp
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

from app_config import AppConfig, configure_torch_runtime
from detection_app import DetectionApp


def select_source(config: AppConfig) -> str:
    """Show a menu for the user to select the video source."""
    print("\n" + "=" * 50)
    print("Motorcycle Rider & Helmet Detection")
    print("=" * 50)
    print("Select video source:")
    print(f"[1] Video file")
    print(f"[2] Live RTSP camera")
    print("=" * 50)

    while True:
        try:
            choice = input("Select [1/2] (default=1): ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "1"

        if choice == "" or choice == "1":
            if not config.video_file:
                print("VIDEO_FILE not found in .env, please set it first")
                sys.exit(1)
            print(f" Using video file\n")
            return config.video_file

        elif choice == "2":
            if not config.rtsp_url:
                print("RTSP_URL not found in .env, please set it first")
                sys.exit(1)
            # Enable RTSP low-latency options only when RTSP is selected
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|"
                "analyzeduration;0|probesize;32"
            )
            print(f"Using live RTSP camera\n")
            return config.rtsp_url

        else:
            print("Please enter 1 or 2")


if __name__ == "__main__":
    mp.freeze_support()

    config = AppConfig.from_env()
    camera_url = select_source(config)

    device, device_secondary, use_half = configure_torch_runtime()

    app = DetectionApp(config, camera_url, device, device_secondary, use_half)
    app.start()
