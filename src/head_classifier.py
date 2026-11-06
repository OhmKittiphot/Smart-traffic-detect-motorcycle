# Second stage of the helmet check: the head detector finds each rider's head, and this classifier decides
# helmet / no helmet on the head crop alone. A head is only ~30-60 px in a 1080p frame, so cropping it from
# the full-size frame shows the classifier far more detail than the whole motorcycle squeezed into 224 px.
from typing import List

import numpy as np
import torch
import torch.nn as nn
import torchvision

from .cuda_graphs import GraphedForward, split_batch
from .rider_classifier import _MEAN, _STD, letterbox

CLASSES = ["helmet", "no_helmet"]
HELMET, NO_HELMET = 0, 1
INPUT_SIZE = 96
# Fraction of the head box added on each side of its crop: the helmet's edge and chin strap sit at the box edge
CROP_PAD = 0.15


def build_model(pretrained: bool = False) -> nn.Module:
    # EfficientNet-B0 backbone with a 2-way head (helmet, no helmet).
    weights = torchvision.models.EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained else None
    model = torchvision.models.efficientnet_b0(weights=weights)
    in_features = model.classifier[1].in_features
    model.classifier = nn.Sequential(nn.Dropout(p=0.3), nn.Linear(in_features, len(CLASSES)))
    return model


def crop_head(frame: np.ndarray, box) -> np.ndarray:
    # The head box with CROP_PAD around it, cut from the frame (at least 1 px: YOLO can return a box of
    # zero width or height).
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box[:4]
    pad_w, pad_h = (x2 - x1) * CROP_PAD, (y2 - y1) * CROP_PAD
    left, top = min(w - 1, max(0, int(x1 - pad_w))), min(h - 1, max(0, int(y1 - pad_h)))
    return frame[top:max(top + 1, min(h, int(y2 + pad_h))), left:max(left + 1, min(w, int(x2 + pad_w)))]


class HeadClassifier:
    # P(no helmet) for head crops.

    def __init__(self, weights_path: str, device: str = "cpu", use_half: bool = False):
        self.device = torch.device(device)
        self.dtype = torch.float16 if use_half and self.device.type == "cuda" else torch.float32
        self.model = build_model(pretrained=False)
        ckpt = torch.load(weights_path, map_location="cpu", weights_only=True)
        self.model.load_state_dict(ckpt["model"] if "model" in ckpt else ckpt)
        self.model.eval().to(self.device, self.dtype)
        self.mean = torch.tensor(_MEAN, device=self.device, dtype=self.dtype).view(1, 3, 1, 1)
        self.std = torch.tensor(_STD, device=self.device, dtype=self.dtype).view(1, 3, 1, 1)
        self.on_gpu = self.device.type == "cuda"
        self.forward = GraphedForward(self.model) if self.on_gpu else self.model

    def _to_batch(self, crops_bgr: List[np.ndarray]) -> torch.Tensor:
        # The crops letterboxed (on the CPU) and normalized as RGB (on the device).
        pixels = torch.from_numpy(np.stack([letterbox(c, INPUT_SIZE) for c in crops_bgr])).to(self.device)
        rgb = pixels.flip(-1).permute(0, 3, 1, 2).to(self.dtype) / 255.0   # (N, H, W, BGR) -> (N, RGB, H, W)
        return (rgb - self.mean) / self.std

    @torch.inference_mode()
    def no_helmet_probs(self, crops_bgr: List[np.ndarray]) -> np.ndarray:
        # (N,) probability that each head has no helmet; on a GPU in padded parts, for its CUDA graphs.
        if not crops_bgr:
            return np.zeros(0, dtype=np.float32)
        batch = self._to_batch(crops_bgr)
        if not self.on_gpu:
            logits = self.forward(batch)
        else:
            parts = []
            for part, size in split_batch(batch):
                padding = part[:1].expand(size - len(part), -1, -1, -1)
                parts.append(self.forward(torch.cat([part, padding]))[:len(part)])
            logits = torch.cat(parts)
        return logits.float().softmax(-1)[:, NO_HELMET].cpu().numpy()
