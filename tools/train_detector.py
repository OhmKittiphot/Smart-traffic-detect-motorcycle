"""
Train the motorcycle(+riders) detector on datasets/helmet_det (built by tools/build_helmet_dataset.py).

Usage:
  python tools/train_detector.py
  python tools/train_detector.py --model yolo11s.pt --imgsz 640 --epochs 40
Output:
  runs/detect/motorcycle_det/weights/best.pt   (copy to models/ and set MOTORCYCLE_MODEL_PATH)
"""
import argparse

from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/helmet_det/data.yaml")
    ap.add_argument("--model", default="models/yolo26s.pt", help="COCO-pretrained start weights")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=960, help="larger helps with small, far-away bikes")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--name", default="motorcycle_det")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    if args.resume:
        YOLO(f"runs/detect/{args.name}/weights/last.pt").train(resume=True)
        return

    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        name=args.name,
        patience=15,
        workers=2,
        cos_lr=True,
        close_mosaic=10,
        fliplr=0.5,
        degrees=0.0,
        exist_ok=True,
    )
    metrics = model.val(data=args.data, split="test", imgsz=args.imgsz)
    print("TEST mAP50:", metrics.box.map50, "mAP50-95:", metrics.box.map)


if __name__ == "__main__":
    main()
