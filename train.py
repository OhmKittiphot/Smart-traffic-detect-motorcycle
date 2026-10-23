from ultralytics import YOLO

model = YOLO("models/yolo11s.pt")

model.train(
    data="motorcycle-license-plate/data.yaml",
    epochs=100,
    imgsz=640,
    batch=16,
    resume=True
)