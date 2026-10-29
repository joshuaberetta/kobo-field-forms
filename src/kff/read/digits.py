"""Read one handwritten digit per comb cell, offline: HOG + pixel features, a small MLP
(numpy at inference: two matrix products).

20 classes: Western digits 0–9 and Eastern Arabic ٠–٩, each mapped to its ASCII value.
Which classes count depends on the sheet's scripts (`scripts.toml` digits), so a Latin
sheet never reads an Eastern Arabic digit. On a sheet that allows both, a ring may be a
Western 0 or an Arabic ٥ (5): the two are kept apart as classes, and when they compete
the reading goes to review.

The model is trained by `scripts/train_digits.py` and kept in `read/models/`.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import cv2
import numpy as np

# A small MLP (features → 256 ReLU → 20 softmax), with the feature means and standard
# deviations it was trained with.
MODEL = Path(__file__).parent / "models" / "digits.npz"
CLASSES = list("0123456789") + list("٠١٢٣٤٥٦٧٨٩")
VALUES = [str(i % 10) for i in range(20)]
# A cell crop is about 4.6 × 6.1 mm at 8 px/mm (scan.register.PX_PER_MM).
CELL_PX = (37, 49)
BLANK_INK = 0.012


@dataclass
class DigitReading:
    value: str | None  # ASCII digit, or None for an empty cell
    confidence: float
    written: str | None = None  # the class read (`٣` or `3`)
    alternatives: tuple[tuple[str, float], ...] = ()


def ink_mask(gray: np.ndarray) -> np.ndarray:
    """Ink as 0–1 floats: darker than the paper, scaled by the darkest stroke.

    The paper level comes from the crop's brightest pixels, but never below 180: a box
    filled solid has no paper left in it, and must read as ink, not as blank paper.
    """
    g = gray.astype(np.float32)
    paper = max(float(np.percentile(g, 90)), 180.0)
    darkest = min(float(g.min()), paper - 1)
    if paper - darkest < 40:
        return np.zeros_like(g)
    return np.clip((paper - g) / (paper - darkest), 0, 1)


def normalize(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """A cell crop → (28×28 MNIST-style image, size features), or None if blank."""
    ink = ink_mask(gray)
    solid = ink > 0.45
    if solid.mean() < BLANK_INK:
        return None
    # Drop specks (scanner dust, JPEG noise) and anything touching the crop's edge,
    # which is a box outline or a neighbour's stroke rather than this digit.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(solid.astype(np.uint8), 8)
    h, w = solid.shape
    keep = np.zeros_like(solid)
    min_area = max(4, int(0.004 * h * w))
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        touches = x == 0 or y == 0 or x + bw == w or y + bh == h
        if area >= min_area and not (touches and area < 0.02 * h * w):
            keep |= labels == i
    if keep.sum() < min_area:
        return None
    ys, xs = np.nonzero(keep)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    digit = ink[y0:y1, x0:x1] * keep[y0:y1, x0:x1]
    size = np.array([(y1 - y0) / h, (x1 - x0) / w], dtype=np.float32)

    dh, dw = digit.shape
    scale = 20.0 / max(dh, dw)
    resized = cv2.resize(
        digit, (max(1, round(dw * scale)), max(1, round(dh * scale))), interpolation=cv2.INTER_AREA
    )
    out = np.zeros((28, 28), np.float32)
    rh, rw = resized.shape
    total = resized.sum()
    cy, cx = np.array(np.nonzero(resized > 0)).mean(axis=1) if total else (rh / 2, rw / 2)
    top = int(np.clip(round(14 - cy), 0, 28 - rh))
    left = int(np.clip(round(14 - cx), 0, 28 - rw))
    out[top : top + rh, left : left + rw] = resized
    return out, size


@cache
def _hog() -> cv2.HOGDescriptor:
    return cv2.HOGDescriptor((28, 28), (14, 14), (7, 7), (7, 7), 9)


def features(img28: np.ndarray, size: np.ndarray) -> np.ndarray:
    u8 = np.clip(img28 * 255, 0, 255).astype(np.uint8)
    hog = _hog().compute(u8).ravel()
    small = cv2.resize(img28, (14, 14), interpolation=cv2.INTER_AREA).ravel()
    return np.concatenate([hog, small, size * 2]).astype(np.float32)


@cache
def model() -> dict[str, np.ndarray]:
    if not MODEL.exists():
        raise FileNotFoundError(f"{MODEL} is missing; run scripts/train_digits.py")
    with np.load(MODEL) as data:
        return {k: data[k] for k in data.files}


def predict_proba(x: np.ndarray, weights: dict[str, np.ndarray] | None = None) -> np.ndarray:
    """Class probabilities for feature rows."""
    w = weights or model()
    z = (x - w["mean"]) / w["std"]
    h = np.maximum(z @ w["w1"] + w["b1"], 0)
    logits = h @ w["w2"] + w["b2"]
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def read_digits(crops: list[np.ndarray], digit_sets: list[str]) -> list[DigitReading]:
    """Read cells; `digit_sets` (e.g. ["0123456789", "٠١٢٣٤٥٦٧٨٩"]) says which classes
    count."""
    allowed = np.array([any(c in s for s in digit_sets) for c in CLASSES])
    out: list[DigitReading | None] = []
    feats: list[np.ndarray] = []
    for crop in crops:
        norm = normalize(crop)
        if norm is None:
            out.append(DigitReading(None, 1.0 - _ink_doubt(crop)))
        else:
            out.append(None)
            feats.append(features(*norm))
    if feats:
        probs = predict_proba(np.stack(feats)) * allowed
        probs = probs / probs.sum(axis=1, keepdims=True)
        it = iter(probs)
        for i, r in enumerate(out):
            if r is not None:
                continue
            p = next(it)
            by_value: dict[str, float] = {}
            for cls, v in enumerate(VALUES):
                by_value[v] = by_value.get(v, 0.0) + float(p[cls])
            ranked = sorted(by_value.items(), key=lambda kv: -kv[1])
            best_cls = int(np.argmax(p))
            out[i] = DigitReading(ranked[0][0], ranked[0][1], CLASSES[best_cls], tuple(ranked[1:3]))
    return [r for r in out if r is not None]


def _ink_doubt(crop: np.ndarray) -> float:
    """How unsure "blank" is: a faint mark just under the threshold isn't certain."""
    ratio = float((ink_mask(crop) > 0.45).mean())
    return min(1.0, ratio / BLANK_INK) * 0.5
