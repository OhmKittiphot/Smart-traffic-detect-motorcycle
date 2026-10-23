import os
import sys
import traceback

import multiprocessing as mp
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

from app_config import AppConfig, configure_torch_runtime
from detection_app import DetectionApp


def select_source(config: AppConfig) -> str:
    #Show a menu for the user to select the video source
    print("\n" + "=" * 50)
    print("Motorcycle License Plate Detection")
    print("=" * 50)
    print("Select video source:")
    print("[1] Live RTSP camera")
    print("[2] Video file")
    print("=" * 50)

    while True:
        try:
            choice = input("Select [1/2] (default=2, q=quit): ").strip().lower()
        except EOFError:
            choice = ""   # no stdin (e.g. piped/non-interactive): use the default
        except KeyboardInterrupt:
            print("\nExiting...")
            sys.exit(0)

        if choice == "1":
            if not config.rtsp_url:
                print("RTSP_URL not found in .env, please set it first")
                sys.exit(1)
            # Enable RTSP low-latency options
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|"
                "analyzeduration;0|probesize;32"
            )
            print(" > Using live RTSP camera\n")
            return config.rtsp_url

        elif choice=="" or choice == "2":
            if not config.video_file:
                print("VIDEO_FILE not found in .env, please set it first")
                sys.exit(1)
            if not os.path.isfile(config.video_file):
                print(f"VIDEO_FILE does not exist: {config.video_file}")
                sys.exit(1)
            print(" > Using video file\n")
            return config.video_file
        elif choice == "q":
            print("Exiting...")
            sys.exit(0)
        else:
            print("Please enter 1 or 2")
    

if __name__ == "__main__":
    mp.freeze_support()
    # Thai plate text crashes print() when stdout is redirected on Windows (cp1252 default)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    config = AppConfig.from_env()
    video_camera = select_source(config)

    device, use_half = configure_torch_runtime()

    try:
        app = DetectionApp(config, video_camera, device, use_half)
        app.start()
    except KeyboardInterrupt:
        print("\nInterrupted. Exiting...")
    except Exception as e:
        print(f"\n[Fatal] {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
