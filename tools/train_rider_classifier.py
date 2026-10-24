"""
Train the rider classifier (rider count + helmet per position) on crops from
tools/build_helmet_dataset.py.

Usage:
  python tools/train_rider_classifier.py --epochs 20
Output:
  runs/rider_cls/<name>/best.pt   (copy to models/ and set RIDER_MODEL_PATH)
"""
import argparse
import csv
import os
import random
import sys
import time

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from rider_classifier import (EMPTY, NO_HELMET, NUM_STATES, POSITIONS, build_model,  # noqa: E402
                              decode_logits, letterbox, to_tensor)


class CropDataset(Dataset):
    def __init__(self, root, rows, train):
        self.root = root
        self.rows = rows
        self.train = train

    def __len__(self):
        return len(self.rows)

    def _augment(self, img):
        h, w = img.shape[:2]
        # Simulate imprecise detector boxes: random shrink on each side and random padding
        if random.random() < 0.8:
            dx1, dx2 = (int(w * random.uniform(0, 0.08)) for _ in range(2))
            dy1, dy2 = (int(h * random.uniform(0, 0.08)) for _ in range(2))
            img = img[dy1:h - dy2 or h, dx1:w - dx2 or w]
            px, py = int(w * random.uniform(0, 0.1)), int(h * random.uniform(0, 0.1))
            img = cv2.copyMakeBorder(img, py, py, px, px, cv2.BORDER_REPLICATE)
        if random.random() < 0.5:
            img = cv2.flip(img, 1)
        # Low resolution, like far-away bikes
        if random.random() < 0.3:
            s = random.uniform(0.3, 0.7)
            small = cv2.resize(img, (max(8, int(img.shape[1] * s)), max(8, int(img.shape[0] * s))))
            img = cv2.resize(small, (img.shape[1], img.shape[0]))
        # Brightness / contrast
        if random.random() < 0.8:
            a, b = random.uniform(0.7, 1.3), random.uniform(-30, 30)
            img = np.clip(img.astype(np.float32) * a + b, 0, 255).astype(np.uint8)
        if random.random() < 0.2:
            img = cv2.GaussianBlur(img, (3, 3), 0)
        return img

    def __getitem__(self, i):
        path, states = self.rows[i]
        img = cv2.imread(os.path.join(self.root, path))
        if self.train:
            img = self._augment(img)
        return to_tensor(letterbox(img)), torch.tensor(states, dtype=torch.long)


def load_rows(root):
    rows = {"train": [], "val": [], "test": []}
    with open(os.path.join(root, "labels.csv"), newline="") as f:
        for r in csv.DictReader(f):
            rows[r["split"]].append((r["path"], [int(r[p]) for p in POSITIONS]))
    return rows


def class_weights(rows):
    """Per-position inverse-sqrt-frequency weights, so rare states (e.g. P2 no helmet) still count.

    The most common state gets weight 1 and rare ones at most MAX_WEIGHT. Keeping the spread small
    matters: with label smoothing every sample also pays w_c * -log p_c for *every* class c, so a
    huge weight on a rare class pushes all predictions toward it. States never seen (driver
    "empty") get weight 1.
    """
    max_weight = 5.0
    counts = np.zeros((len(POSITIONS), NUM_STATES))
    for _, states in rows:
        for p, s in enumerate(states):
            counts[p, s] += 1
    w = np.sqrt(counts.max(1, keepdims=True) / np.maximum(counts, 1))
    w = np.where(counts > 0, np.clip(w, 1.0, max_weight), 1.0)
    return torch.tensor(w, dtype=torch.float32)


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    preds, targets = [], []
    for x, y in loader:
        with torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
            logits = model(x.to(device, non_blocking=True))
        preds.append(decode_logits(logits).argmax(-1).cpu())
        targets.append(y)
    p, t = torch.cat(preds), torch.cat(targets)

    riders_p, riders_t = (p != EMPTY).sum(1), (t != EMPTY).sum(1)
    nohelm_p, nohelm_t = (p == NO_HELMET).sum(1), (t == NO_HELMET).sum(1)
    over_p, over_t = riders_p > 2, riders_t > 2  # more than one passenger

    def prf(pred, true):
        tp = (pred & true).sum().item()
        prec = tp / max(1, pred.sum().item())
        rec = tp / max(1, true.sum().item())
        return prec, rec

    m = {
        "exact": (p == t).all(1).float().mean().item(),
        "rider_count_acc": (riders_p == riders_t).float().mean().item(),
        "no_helmet_count_acc": (nohelm_p == nohelm_t).float().mean().item(),
        "over_prec_rec": prf(over_p, over_t),
        "any_no_helmet_prec_rec": prf(nohelm_p > 0, nohelm_t > 0),
    }
    for i, pos in enumerate(POSITIONS):
        m[f"acc_{pos}"] = (p[:, i] == t[:, i]).float().mean().item()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/rider_cls")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--name", default="train")
    ap.add_argument("--init", default="", help="start from these weights (e.g. a previous best.pt) instead of ImageNet")
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = os.path.join("runs", "rider_cls", args.name)
    os.makedirs(out_dir, exist_ok=True)

    rows = load_rows(args.data)
    print({k: len(v) for k, v in rows.items()})
    train_dl = DataLoader(CropDataset(args.data, rows["train"], True), batch_size=args.batch, shuffle=True,
                          num_workers=args.workers, pin_memory=True, drop_last=True, persistent_workers=True)
    val_dl = DataLoader(CropDataset(args.data, rows["val"], False), batch_size=args.batch * 2,
                        num_workers=args.workers, pin_memory=True, persistent_workers=True)

    model = build_model(pretrained=not args.init)
    if args.init:
        model.load_state_dict(torch.load(args.init, map_location="cpu")["model"])
    model = model.to(device)
    weights = class_weights(rows["train"]).to(device)
    losses = [nn.CrossEntropyLoss(weight=weights[i], label_smoothing=0.05) for i in range(len(POSITIONS))]
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.epochs * len(train_dl),
                                                pct_start=0.1)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")

    best = -1.0
    log_path = os.path.join(out_dir, "results.csv")
    with open(log_path, "w") as f:
        f.write("epoch,loss,exact,rider_count_acc,no_helmet_count_acc,over_prec,over_rec,"
                + ",".join(f"acc_{p}" for p in POSITIONS) + "\n")

    for epoch in range(1, args.epochs + 1):
        model.train()
        t0, total, n = time.time(), 0.0, 0
        for x, y in train_dl:
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            with torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
                logits = model(x).view(-1, len(POSITIONS), NUM_STATES)
                loss = sum(losses[i](logits[:, i].float(), y[:, i]) for i in range(len(POSITIONS)))
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            total += loss.item() * x.size(0)
            n += x.size(0)

        m = evaluate(model, val_dl, device)
        print(f"epoch {epoch}/{args.epochs}  loss {total / n:.4f}  exact {m['exact']:.3f}  "
              f"riders {m['rider_count_acc']:.3f}  no_helmet_cnt {m['no_helmet_count_acc']:.3f}  "
              f"overload P/R {m['over_prec_rec'][0]:.2f}/{m['over_prec_rec'][1]:.2f}  "
              f"({time.time() - t0:.0f}s)", flush=True)
        with open(log_path, "a") as f:
            f.write(f"{epoch},{total / n:.4f},{m['exact']:.4f},{m['rider_count_acc']:.4f},"
                    f"{m['no_helmet_count_acc']:.4f},{m['over_prec_rec'][0]:.4f},{m['over_prec_rec'][1]:.4f},"
                    + ",".join(f"{m[f'acc_{p}']:.4f}" for p in POSITIONS) + "\n")

        ckpt = {"model": model.state_dict(), "epoch": epoch, "metrics": m, "positions": POSITIONS}
        torch.save(ckpt, os.path.join(out_dir, "last.pt"))
        if m["exact"] > best:
            best = m["exact"]
            torch.save(ckpt, os.path.join(out_dir, "best.pt"))

    if rows["test"]:
        test_dl = DataLoader(CropDataset(args.data, rows["test"], False), batch_size=args.batch * 2,
                             num_workers=args.workers)
        model.load_state_dict(torch.load(os.path.join(out_dir, "best.pt"))["model"])
        print("TEST:", evaluate(model, test_dl, device))


if __name__ == "__main__":
    main()
