import os
import sys
import traceback
import multiprocessing as mp
from unittest import case
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

from .app_config import AppConfig, configure_torch_runtime
from .desktop_viewer import DesktopViewer
from .detection_app import DetectionApp


def select_source(config: AppConfig):
    print("\n" + "=" * 50)
    print("Motorcycle Rider, Helmet & License Plate Detection")
    print("=" * 50)
    print("Select video source:")
    print("[1] Video file")
    print("[2] Live RTSP camera")
    print("=" * 50)

    while True:
        try:
            choice = input("Select [1/2] (default=1, q=quit): ").strip().lower()
        except EOFError:
            choice = "1"
        except KeyboardInterrupt:
            print("\nExiting...")
            sys.exit(0)

        match (choice):
            case "1":
                if not config.video_file:
                    print("VIDEO_FILE not found in .env, please set it first")
                    sys.exit(1)
                if not os.path.isfile(config.video_file):
                    print(f"VIDEO_FILE does not exist: {config.video_file}")
                    sys.exit(1)
                print(" > Using video file\n")
                return config.video_file

            case "2":
                if not config.rtsp_url:
                    print("RTSP_URL not found in .env, please set it first")
                    sys.exit(1)
                print(" > Using live RTSP camera\n")
                return config.rtsp_url

            case "q":
                print("Exiting...")
                sys.exit(0)
            case _:
                print("Please enter 1 or 2")

        if __name__ == "__main__":
            mp.freeze_support()
            print("Please enter 1 or 2")

if not __package__:
    sys.exit("Run it as part of the src package, from the repository root: venv\\Scripts\\python -m src.main")

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
        DesktopViewer(DetectionApp(config, video_camera, device, use_half)).run()
    except KeyboardInterrupt:
        print("\nInterrupted. Exiting...")
    except Exception as e:
        print(f"\n[Fatal] {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
