"""
Build training data from the HELMET dataset (Myanmar traffic, 910 clips x 100 frames, 1920x1080).

Each annotated motorcycle box carries a label like "DHelmetP1NoHelmetP2NoHelmet":
  D = driver, P0 = passenger in front of the driver (child), P1..P3 = passengers behind.

Outputs:
  datasets/helmet_det/   YOLO detection dataset, single class "motorcycle" (bike + riders box)
  datasets/rider_cls/    motorcycle crops + labels.csv with per-position state
                         (0 = empty, 1 = helmet, 2 = no helmet) for D, P0, P1, P2, P3

Only every FRAME_STEP-th frame is downloaded, read straight out of the remote zip parts with
HTTP range requests, so the full ~29 GB archive never has to be stored.

Usage:
  python tools/build_helmet_dataset.py                 # all 7 parts
  python tools/build_helmet_dataset.py --parts 7 --max-clips 3   # quick test
"""
import argparse
import csv
import io
import os
import re
import struct
import zipfile
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np
import requests
from tqdm import tqdm

BASE_URL = "https://huggingface.co/datasets/AryanRajMaurya/Helmets/resolve/main/osfstorage/HELMET_DATASET"
SRC_W, SRC_H = 1920, 1080
OUT_W, OUT_H = 1280, 720
POSITIONS = ["D", "P0", "P1", "P2", "P3"]
SPLIT_MAP = {"training": "train", "validation": "val", "test": "test"}
_LABEL_RE = re.compile(r"(D|P0|P1|P2|P3)(Helmet|NoHelmet)")


class HttpFile(io.RawIOBase):
    """Minimal seekable read-only file over HTTP range requests (enough for zipfile)."""

    def __init__(self, url: str):
        self.session = requests.Session()
        r = self.session.head(url, allow_redirects=True, timeout=30)
        r.raise_for_status()
        self.url = r.url
        self.size = int(r.headers["Content-Length"])
        self.pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        data = fetch_range(self.session, self.url, self.pos, min(self.size, self.pos + n) - 1)
        self.pos += len(data)
        return data


def fetch_range(session, url, start, end, retries=5):
    for attempt in range(retries):
        try:
            r = session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=60)
            r.raise_for_status()
            return r.content
        except Exception:
            if attempt == retries - 1:
                raise


def read_member(session, url, info: zipfile.ZipInfo) -> bytes:
    """Fetch one zip member with a single range request (local header + data)."""
    # Local header is 30 bytes + filename + extra; extra field length can differ from the
    # central directory, so over-fetch a little and parse the real lengths.
    buf = fetch_range(session, url, info.header_offset,
                      info.header_offset + 30 + len(info.filename.encode()) + 512 + info.compress_size)
    name_len, extra_len = struct.unpack("<HH", buf[26:30])
    start = 30 + name_len + extra_len
    data = buf[start:start + info.compress_size]
    if info.compress_type == zipfile.ZIP_DEFLATED:
        data = zlib.decompressobj(-15).decompress(data)
    return data


def parse_rider_label(label: str):
    """'DHelmetP1NoHelmet' -> [1, 0, 2, 0, 0] in POSITIONS order."""
    states = [0] * len(POSITIONS)
    for pos, status in _LABEL_RE.findall(label):
        states[POSITIONS.index(pos)] = 1 if status == "Helmet" else 2
    return states


def load_annotations(raw_dir):
    """Return {(clip, frame_id): [(x, y, w, h, track_id, states), ...]} and {clip: split}."""
    ann_zip = os.path.join(raw_dir, "annotation.zip")
    split_csv = os.path.join(raw_dir, "data_split.csv")
    for name, path in (("annotation.zip", ann_zip), ("data_split.csv", split_csv)):
        if not os.path.exists(path):
            print(f"Downloading {name}...")
            r = requests.get(f"{BASE_URL}/{name}", timeout=120)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)

    with open(split_csv, newline="") as f:
        splits = {row["video_id"]: SPLIT_MAP[row["Set"]] for row in csv.DictReader(f)}

    anns = defaultdict(list)
    with zipfile.ZipFile(ann_zip) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            clip = os.path.splitext(os.path.basename(name))[0]
            for row in csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8")):
                anns[(clip, int(row["frame_id"]))].append((
                    float(row["x"]), float(row["y"]), float(row["w"]), float(row["h"]),
                    row["track_id"], parse_rider_label(row["label"]),
                ))
    return anns, splits


def process_frame(img_bytes, clip, frame_id, split, boxes, out_root, min_crop):
    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return []
    src_h, src_w = img.shape[:2]
    img = cv2.resize(img, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
    sx, sy = OUT_W / src_w, OUT_H / src_h

    stem = f"{clip}_{frame_id:03d}"
    det_dir = os.path.join(out_root, "helmet_det")
    cv2.imwrite(os.path.join(det_dir, "images", split, stem + ".jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])

    yolo_lines, cls_rows = [], []
    for (x, y, w, h, track_id, states) in boxes:
        x1, y1 = max(0.0, x * sx), max(0.0, y * sy)
        x2, y2 = min(OUT_W, (x + w) * sx), min(OUT_H, (y + h) * sy)
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        yolo_lines.append(f"0 {(x1 + x2) / 2 / OUT_W:.6f} {(y1 + y2) / 2 / OUT_H:.6f} "
                          f"{(x2 - x1) / OUT_W:.6f} {(y2 - y1) / OUT_H:.6f}")

        if min(x2 - x1, y2 - y1) < min_crop:
            continue
        crop = img[int(y1):int(y2), int(x1):int(x2)]
        crop_name = f"{stem}_{track_id}.jpg"
        cv2.imwrite(os.path.join(out_root, "rider_cls", split, crop_name), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        cls_rows.append([f"{split}/{crop_name}", split, *states])

    with open(os.path.join(det_dir, "labels", split, stem + ".txt"), "w") as f:
        f.write("\n".join(yolo_lines))
    return cls_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="datasets")
    ap.add_argument("--parts", default="1,2,3,4,5,6,7")
    ap.add_argument("--frame-step", type=int, default=5, help="keep frames 1, 1+step, 1+2*step, ...")
    ap.add_argument("--min-crop", type=int, default=40, help="min crop side (px at 1280x720) for the classifier")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--max-clips", type=int, default=0, help="limit clips per part (0 = all), for testing")
    args = ap.parse_args()

    raw_dir = os.path.join(args.out, "helmet_raw")
    os.makedirs(raw_dir, exist_ok=True)
    for split in SPLIT_MAP.values():
        os.makedirs(os.path.join(args.out, "helmet_det", "images", split), exist_ok=True)
        os.makedirs(os.path.join(args.out, "helmet_det", "labels", split), exist_ok=True)
        os.makedirs(os.path.join(args.out, "rider_cls", split), exist_ok=True)

    anns, splits = load_annotations(raw_dir)
    print(f"Annotations: {len(anns)} frames, {len(splits)} clips")

    labels_csv = os.path.join(args.out, "rider_cls", "labels.csv")
    done = set()
    if os.path.exists(labels_csv):
        with open(labels_csv, newline="") as f:
            done = {row["path"] for row in csv.DictReader(f)}
    new_file = not os.path.exists(labels_csv)
    csv_f = open(labels_csv, "a", newline="")
    writer = csv.writer(csv_f)
    if new_file:
        writer.writerow(["path", "split", *POSITIONS])

    for part in [int(p) for p in args.parts.split(",")]:
        url = f"{BASE_URL}/image/part_{part}.zip"
        hf = HttpFile(url)
        z = zipfile.ZipFile(hf)

        by_clip = defaultdict(list)
        for info in z.infolist():
            m = re.match(r".*/([^/]+)/(\d+)\.jpg$", info.filename)
            if m and (int(m.group(2)) - 1) % args.frame_step == 0:
                by_clip[m.group(1)].append((int(m.group(2)), info))
        clips = sorted(by_clip)
        if args.max_clips:
            clips = clips[:args.max_clips]

        jobs = []
        for clip in clips:
            split = splits.get(clip)
            if split is None:
                continue
            for frame_id, info in by_clip[clip]:
                stem = f"{clip}_{frame_id:03d}"
                if os.path.exists(os.path.join(args.out, "helmet_det", "labels", split, stem + ".txt")):
                    continue  # resume support
                jobs.append((clip, frame_id, split, info))

        print(f"part_{part}: {len(clips)} clips, {len(jobs)} frames to fetch")

        def work(job):
            clip, frame_id, split, info = job
            data = read_member(hf.session, hf.url, info)
            return process_frame(data, clip, frame_id, split, anns.get((clip, frame_id), []), args.out, args.min_crop)

        with ThreadPoolExecutor(args.workers) as ex:
            futures = [ex.submit(work, j) for j in jobs]
            for fut in tqdm(as_completed(futures), total=len(futures), desc=f"part_{part}"):
                try:
                    rows = fut.result()
                except Exception as e:
                    print(f"  frame failed: {e}")
                    continue
                writer.writerows(r for r in rows if r[0] not in done)
            csv_f.flush()

    csv_f.close()

    with open(os.path.join(args.out, "helmet_det", "data.yaml"), "w") as f:
        f.write(f"path: {os.path.abspath(os.path.join(args.out, 'helmet_det'))}\n"
                "train: images/train\nval: images/val\ntest: images/test\n\n"
                "nc: 1\nnames: ['motorcycle']\n")
    print("Done.")


if __name__ == "__main__":
    main()
