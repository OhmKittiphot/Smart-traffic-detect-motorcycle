import multiprocessing as mp
import os
import sys
import traceback
import warnings

from pathlib import Path

VIDEO_DIR = "input"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".ts")

if not __package__:
    sys.exit("Run it as part of the src package, from the repository root: venv\\Scripts\\python -m src.main")

warnings.filterwarnings("ignore", category=DeprecationWarning)

def list_video_files(folder=VIDEO_DIR) -> list:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)

def _ask(prompt: str, default: str) -> str:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return default
    except KeyboardInterrupt:
        answer = "q"
    if answer == "q":
        print("\nExiting...")
        sys.exit(0)
    return answer or default


def select_video_file() -> str:
    files = list_video_files()
    if not files:
        print(f"No video files in {VIDEO_DIR}/ ({', '.join(VIDEO_EXTENSIONS)}); put one there first")
        sys.exit(1)
    print(f"Video files in {VIDEO_DIR}/:")
    for number, name in enumerate(files, 1):
        print(f"[{number}] {name}")
    while True:
        choice = _ask(f"Select [1-{len(files)}] (default=1, q=quit): ", "1")
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            name = files[int(choice) - 1]
            print(f" > Using video file {name}\n")
            return os.path.join(VIDEO_DIR, name)
        print(f"Please enter a number from 1 to {len(files)}")


def select_source(config) -> str:
    print("\n" + "=" * 50)
    print("Motorcycle Rider, Helmet & License Plate Detection")
    print("=" * 50)
    print("Select video source:")
    print(f"[1] Video file in {VIDEO_DIR}/")
    print("[2] Live RTSP camera")
    print("=" * 50)

    while True:
        match _ask("Select [1/2] (default=1, q=quit): ", "1"):
            case "1":
                return select_video_file()
            case "2":
                if not config.rtsp_url:
                    print("RTSP_URL not found in .env, please set it first")
                    sys.exit(1)
                print(" > Using live RTSP camera\n")
                return config.rtsp_url
            case _:
                print("Please enter 1 or 2")


if __name__ == "__main__":
    mp.freeze_support()

    from .app_config import AppConfig, configure_torch_runtime
    from .desktop_viewer import DesktopViewer
    from .detection_app import DetectionApp

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
