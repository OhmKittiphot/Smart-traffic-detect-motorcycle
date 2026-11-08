# Second stage of the helmet check: the head detector finds each rider's head, and this classifier decides
# helmet / no helmet on the head crop alone. A head is only ~30-60 px in a 1080p frame, so cropping it from
# the full-size frame shows the classifier far more detail than the whole motorcycle squeezed into 224 px.
from typing import List

import numpy as np
import torch

from .crop_classifier import CropClassifier

CLASSES = ["helmet", "no_helmet"]
NO_HELMET = CLASSES.index("no_helmet")
INPUT_SIZE = 96
# Fraction of the head box added on each side of its crop: the helmet's edge and chin strap sit at the box edge
CROP_PAD = 0.15


def crop_head(frame: np.ndarray, box) -> np.ndarray:
    # The head box with CROP_PAD around it, cut from the frame (at least 1 px: YOLO can return a box of
    # zero width or height).
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box[:4]
    pad_w, pad_h = (x2 - x1) * CROP_PAD, (y2 - y1) * CROP_PAD
    left, top = min(w - 1, max(0, int(x1 - pad_w))), min(h - 1, max(0, int(y1 - pad_h)))
    return frame[top:max(top + 1, min(h, int(y2 + pad_h))), left:max(left + 1, min(w, int(x2 + pad_w)))]


class HelmetClassifier(CropClassifier):
    # P(no helmet) for head crops.

    def __init__(self, weights_path: str, device: str = "cpu", use_half: bool = False):
        super().__init__(weights_path, len(CLASSES), INPUT_SIZE, device, use_half)

    @torch.inference_mode()
    def no_helmet_probs(self, crops_bgr: List[np.ndarray]) -> np.ndarray:
        # (N,) probability that each head has no helmet.
        if not crops_bgr:
            return np.zeros(0, dtype=np.float32)
        return self.logits(crops_bgr).float().softmax(-1)[:, NO_HELMET].cpu().numpy()
