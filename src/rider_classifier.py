from typing import Dict, List

import numpy as np
import torch

from .crop_classifier import CropClassifier

# Rider positions on a motorcycle (HELMET dataset convention):
#   D = driver, P0 = passenger in front of the driver (child), P1..P3 = passengers behind
POSITIONS = ["D", "P0", "P1", "P2", "P3"]
# Per-position state
EMPTY, HELMET, NO_HELMET = 0, 1, 2
NUM_STATES = 3

INPUT_SIZE = 224


def decode_logits(logits: torch.Tensor) -> torch.Tensor:
    # (N, 15) logits -> (N, 5, 3) probabilities. The driver is always present.
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


class RiderClassifier(CropClassifier):
    # Predicts rider count and helmet use per position from motorcycle crops (one 3-way output per position).

    def __init__(self, weights_path: str, device: str = "cpu", use_half: bool = False):
        super().__init__(weights_path, len(POSITIONS) * NUM_STATES, INPUT_SIZE, device, use_half)

    @torch.inference_mode()
    def predict_probs(self, crops_bgr: List[np.ndarray]) -> np.ndarray:
        # Return (N, 5, 3) per-position state probabilities.
        if not crops_bgr:
            return np.zeros((0, len(POSITIONS), NUM_STATES), dtype=np.float32)
        return decode_logits(self.logits(crops_bgr)).cpu().numpy()
