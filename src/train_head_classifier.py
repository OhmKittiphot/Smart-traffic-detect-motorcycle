# Trains the head classifier on the Helmet / No-Helmet head boxes of a YOLO dataset:
#   venv\Scripts\python -m src.train_head_classifier --data helmet-rider-dataset --out models/head_cls.pt
# Each epoch jitters the head box and shrinks or blurs the crop, like the detector's boxes and far CCTV heads.
import argparse
import os
import random

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from .head_classifier import CROP_PAD, HELMET, INPUT_SIZE, NO_HELMET, build_model
from .rider_classifier import letterbox, to_tensor

# YOLO class name -> head classifier class (other classes, such as "person", are skipped)
CLASS_MAP = {"helmet": HELMET, "no-helmet": NO_HELMET, "no helmet": NO_HELMET, "head": NO_HELMET}
# Margin cut around each head once, so the box can be jittered inside it during training
CACHE_PAD = 0.5
MIN_HEAD_PX = 8


def _class_names(data_dir: str) -> list:
    with open(os.path.join(data_dir, "data.yaml"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("names:"):
                return [n.strip(" '\"").lower() for n in line.split("[", 1)[1].rsplit("]", 1)[0].split(",")]
    raise ValueError("no names: line in data.yaml")


def load_heads(data_dir: str, split: str, names: list) -> list:
    # [(crop with CACHE_PAD margin, head box inside that crop, class)] of every head in one split.
    image_dir, label_dir = os.path.join(data_dir, split, "images"), os.path.join(data_dir, split, "labels")
    heads = []
    for name in sorted(os.listdir(image_dir)):
        label_path = os.path.join(label_dir, os.path.splitext(name)[0] + ".txt")
        image = cv2.imread(os.path.join(image_dir, name))
        if image is None or not os.path.isfile(label_path):
            continue
        h, w = image.shape[:2]
        with open(label_path) as f:
            rows = [line.split() for line in f if line.strip()]
        for cls, cx, cy, bw, bh in ((int(r[0]), *map(float, r[1:5])) for r in rows):
            label = CLASS_MAP.get(names[cls])
            if label is None or bw * w < MIN_HEAD_PX or bh * h < MIN_HEAD_PX:
                continue
            x1, y1, x2, y2 = (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h
            cx1, cy1 = max(0, int(x1 - CACHE_PAD * bw * w)), max(0, int(y1 - CACHE_PAD * bh * h))
            cx2, cy2 = min(w, int(x2 + CACHE_PAD * bw * w)), min(h, int(y2 + CACHE_PAD * bh * h))
            heads.append((image[cy1:cy2, cx1:cx2].copy(), (x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1), label))
    return heads


class HeadCrops(Dataset):
    def __init__(self, heads: list, train: bool):
        self.heads = heads
        self.train = train

    def __len__(self):
        return len(self.heads)

    def _crop(self, image, box):
        # The head box with the inference-time CROP_PAD; in training also shifted and scaled up to 15%.
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        if self.train:
            scale = random.uniform(0.85, 1.15)
            cx = (x1 + x2) / 2 + random.uniform(-0.15, 0.15) * bw
            cy = (y1 + y2) / 2 + random.uniform(-0.15, 0.15) * bh
            bw, bh = bw * scale, bh * scale
            x1, y1, x2, y2 = cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
        h, w = image.shape[:2]
        crop = image[max(0, int(y1 - CROP_PAD * bh)):min(h, int(y2 + CROP_PAD * bh)),
                     max(0, int(x1 - CROP_PAD * bw)):min(w, int(x2 + CROP_PAD * bw))]
        return crop if crop.size else image

    @staticmethod
    def _degrade(crop):
        # Look like a CCTV head: flipped, smaller (then enlarged again), blurred, JPEG-compressed, other light.
        if random.random() < 0.5:
            crop = cv2.flip(crop, 1)
        if random.random() < 0.6:
            size = random.randint(14, 48)
            scale = size / max(crop.shape[:2])
            if scale < 1:
                small_size = (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale)))
                small = cv2.resize(crop, small_size, interpolation=cv2.INTER_AREA)
                crop = cv2.resize(small, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR)
        if random.random() < 0.3:
            crop = cv2.GaussianBlur(crop, (3, 3), 0)
        if random.random() < 0.3:
            _, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, random.randint(30, 80)])
            crop = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        alpha, beta = random.uniform(0.6, 1.4), random.uniform(-30, 30)
        return cv2.convertScaleAbs(crop, alpha=alpha, beta=beta)

    def __getitem__(self, i):
        image, box, label = self.heads[i]
        crop = self._crop(image, box)
        if self.train:
            crop = self._degrade(crop)
        return to_tensor(letterbox(crop, INPUT_SIZE)), label


@torch.no_grad()
def evaluate(model, loader, device) -> dict:
    # Recall of each class and their mean (balanced accuracy).
    model.eval()
    correct, total = np.zeros(2), np.zeros(2)
    for x, y in loader:
        pred = model(x.to(device)).argmax(-1).cpu()
        for c in (HELMET, NO_HELMET):
            total[c] += (y == c).sum().item()
            correct[c] += ((y == c) & (pred == c)).sum().item()
    recall = correct / np.maximum(total, 1)
    return {"helmet_recall": float(recall[HELMET]), "no_helmet_recall": float(recall[NO_HELMET]),
            "balanced_acc": float(recall.mean())}


def main():
    parser = argparse.ArgumentParser(description="Train the head classifier on the head boxes of a YOLO dataset")
    parser.add_argument("--data", default="helmet-rider-dataset")
    parser.add_argument("--out", default="models/head_cls.pt")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    args = parser.parse_args()

    random.seed(0)
    torch.manual_seed(0)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    names = _class_names(args.data)
    train_heads, val_heads = load_heads(args.data, "train", names), load_heads(args.data, "valid", names)
    print(f"heads: train {len(train_heads)}, valid {len(val_heads)}")
    train_loader = DataLoader(HeadCrops(train_heads, train=True), batch_size=args.batch, shuffle=True)
    val_loader = DataLoader(HeadCrops(val_heads, train=False), batch_size=args.batch)

    model = build_model(pretrained=True).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer, args.lr, epochs=args.epochs,
                                                    steps_per_epoch=len(train_loader))
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
    best = -1.0
    for epoch in range(1, args.epochs + 1):
        model.train()
        for x, y in train_loader:
            loss = loss_fn(model(x.to(device)), y.to(device))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            scheduler.step()
        metrics = evaluate(model, val_loader, device)
        print(f"epoch {epoch}: loss {loss.item():.3f} " + " ".join(f"{k} {v:.3f}" for k, v in metrics.items()))
        if metrics["balanced_acc"] > best:
            best = metrics["balanced_acc"]
            torch.save({"model": model.state_dict(), "epoch": epoch, "metrics": metrics}, args.out)
    print(f"best balanced accuracy {best:.3f} -> {args.out}")


if __name__ == "__main__":
    main()
