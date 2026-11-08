# What the two image classifiers (rider_classifier.py, helmet_classifier.py) share: an EfficientNet-B0 with
# its own last layer, crops letterboxed to a square and normalized on the device, and on a GPU batches padded
# to the sizes its CUDA graphs were recorded for.
from typing import List

import cv2
import numpy as np
import torch
import torch.nn as nn
import torchvision

from .cuda_graphs import GraphedForward, split_batch

# ImageNet mean and standard deviation (RGB), the input normalization EfficientNet-B0 was trained with
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)


def build_model(num_outputs: int) -> nn.Module:
    # EfficientNet-B0 backbone with a new last layer of num_outputs.
    model = torchvision.models.efficientnet_b0(weights=None)
    in_features = model.classifier[1].in_features
    model.classifier = nn.Sequential(nn.Dropout(p=0.3), nn.Linear(in_features, num_outputs))
    return model


def letterbox(img_bgr: np.ndarray, size: int) -> np.ndarray:
    # Resize keeping the aspect ratio and pad to a square, so the crop is not stretched (where riders sit
    # on a motorcycle depends on its shape).
    h, w = img_bgr.shape[:2]
    scale = size / max(h, w)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    out = np.full((size, size, 3), 114, dtype=np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    out[top:top + nh, left:left + nw] = resized
    return out


class CropClassifier:
    # A trained EfficientNet-B0 (build_model) that classifies image crops of input_size x input_size.

    def __init__(self, weights_path: str, num_outputs: int, input_size: int, device: str = "cpu",
                 use_half: bool = False):
        self.input_size = input_size
        self.device = torch.device(device)
        self.dtype = torch.float16 if use_half and self.device.type == "cuda" else torch.float32
        self.model = build_model(num_outputs)
        ckpt = torch.load(weights_path, map_location="cpu", weights_only=True)
        self.model.load_state_dict(ckpt["model"] if "model" in ckpt else ckpt)
        self.model.eval().to(self.device, self.dtype)
        self.mean = torch.tensor(_MEAN, device=self.device, dtype=self.dtype).view(1, 3, 1, 1)
        self.std = torch.tensor(_STD, device=self.device, dtype=self.dtype).view(1, 3, 1, 1)
        self.on_gpu = self.device.type == "cuda"
        self.forward = GraphedForward(self.model) if self.on_gpu else self.model

    def _to_batch(self, crops_bgr: List[np.ndarray]) -> torch.Tensor:
        # The crops letterboxed (on the CPU) and normalized as RGB (on the device).
        pixels = torch.from_numpy(np.stack([letterbox(c, self.input_size) for c in crops_bgr])).to(self.device)
        rgb = pixels.flip(-1).permute(0, 3, 1, 2).to(self.dtype) / 255.0   # (N, H, W, BGR) -> (N, RGB, H, W)
        return (rgb - self.mean) / self.std

    def logits(self, crops_bgr: List[np.ndarray]) -> torch.Tensor:
        # The model's output for the crops; on a GPU in padded parts, for its CUDA graphs (split_batch).
        batch = self._to_batch(crops_bgr)
        if not self.on_gpu:
            return self.forward(batch)
        parts = []
        for part, size in split_batch(batch):
            padding = part[:1].expand(size - len(part), -1, -1, -1)
            parts.append(self.forward(torch.cat([part, padding]))[:len(part)])
        return torch.cat(parts)
