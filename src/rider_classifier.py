from typing import Dict, List

import cv2
import numpy as np
import torch
import torch.nn as nn
import torchvision

# Rider positions on a motorcycle (HELMET dataset convention):
#   D = driver, P0 = passenger in front of the driver (child), P1..P3 = passengers behind
POSITIONS = ["D", "P0", "P1", "P2", "P3"]
# Per-position state
EMPTY, HELMET, NO_HELMET = 0, 1, 2
NUM_STATES = 3

INPUT_SIZE = 224
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def build_model(pretrained: bool = False) -> nn.Module:
    """EfficientNet-B0 backbone with one 3-way head per rider position."""
    weights = torchvision.models.EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained else None
    model = torchvision.models.efficientnet_b0(weights=weights)
    in_features = model.classifier[1].in_features
    model.classifier = nn.Sequential(
        nn.Dropout(p=0.3),
        nn.Linear(in_features, len(POSITIONS) * NUM_STATES),
    )
    return model


def letterbox(img_bgr: np.ndarray, size: int = INPUT_SIZE) -> np.ndarray:
    """Resize keeping aspect ratio and pad to a square (rider layout depends on geometry)."""
    h, w = img_bgr.shape[:2]
    scale = size / max(h, w)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    out = np.full((size, size, 3), 114, dtype=np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    out[top:top + nh, left:left + nw] = resized
    return out


def to_tensor(img_bgr: np.ndarray) -> torch.Tensor:
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return torch.from_numpy(((rgb - _MEAN) / _STD).transpose(2, 0, 1).copy())


def decode_logits(logits: torch.Tensor) -> torch.Tensor:
    """(N, 15) logits -> (N, 5, 3) probabilities. The driver is always present."""
    probs = logits.view(-1, len(POSITIONS), NUM_STATES).float().softmax(-1)
    driver = probs[:, 0].clone()
    driver[:, EMPTY] = 0.0
    probs[:, 0] = driver / driver.sum(-1, keepdim=True).clamp_min(1e-6)
    return probs


def summarize(states: List[int]) -> Dict:
    riders = sum(1 for s in states if s != EMPTY)
    return {
        "states": dict(zip(POSITIONS, states)),
        "riders": riders,
        "passengers": max(0, riders - 1),
        "no_helmet": sum(1 for s in states if s == NO_HELMET),
    }


class RiderClassifier:
    """Predicts rider count and helmet use per position from motorcycle crops."""

    def __init__(self, weights_path: str, device: str = "cpu", use_half: bool = False):
        self.device = torch.device(device)
        self.use_half = use_half and self.device.type == "cuda"
        self.model = build_model(pretrained=False)
        ckpt = torch.load(weights_path, map_location="cpu")
        self.model.load_state_dict(ckpt["model"] if "model" in ckpt else ckpt)
        self.model.eval().to(self.device)
        if self.use_half:
            self.model.half()

    @torch.inference_mode()
    def predict_probs(self, crops_bgr: List[np.ndarray]) -> np.ndarray:
        """Return (N, 5, 3) per-position state probabilities."""
        if not crops_bgr:
            return np.zeros((0, len(POSITIONS), NUM_STATES), dtype=np.float32)
        batch = torch.stack([to_tensor(letterbox(c)) for c in crops_bgr]).to(self.device)
        if self.use_half:
            batch = batch.half()
        return decode_logits(self.model(batch)).cpu().numpy()

    def predict(self, crops_bgr: List[np.ndarray]) -> List[Dict]:
        return [summarize(p.argmax(-1).tolist()) for p in self.predict_probs(crops_bgr)]
