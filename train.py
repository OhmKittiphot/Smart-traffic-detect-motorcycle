import argparse

from ultralytics import YOLO


# Heads and plates are small in wide street shots, so both train at 960 (same imgsz the app uses)
TARGETS = {
    "helmet": dict(data="helmet-rider-dataset/data.yaml", name="helmet-rider", batch=4),
    # Single class: motorcycle plates only (built by prepare_plate_dataset.py)
    "plate": dict(data="plate-dataset/data.yaml", name="moto-plate", batch=4),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", choices=TARGETS)
    ap.add_argument("--epochs", type=int, default=100)
    args = ap.parse_args()

    model = YOLO("models/yolo11s.pt")
    model.train(
        **TARGETS[args.target],
        epochs=args.epochs,
        patience=15,
        imgsz=960,
        workers=2,
    )


# Required on Windows: DataLoader workers re-import this file.
if __name__ == "__main__":
    main()
