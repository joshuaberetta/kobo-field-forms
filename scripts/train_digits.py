"""Train the digit model in src/kff/read/models/ (dev time, not at runtime).

  uv run python scripts/train_digits.py [--mnist ~/.cache/kff/mnist] [--epochs 20]

Training data:
- Western digits: MNIST (real handwriting, CC BY-SA 3.0, Yann LeCun and Corinna Cortes),
  from ossci-datasets.s3.amazonaws.com/mnist/, augmented.
- Eastern Arabic digits: rendered from the bundled Noto Naskh Arabic, heavily augmented.
  ⚠ No real handwriting yet (AHDD1/MADBase licence unchecked), so expect these to read
  worse on real sheets than on synthetic ones.
- Western digits rendered from Noto Sans too, for people who print their digits.

The synthetic-sheet handwriting fonts (src/kff/synth/fonts) are left out on purpose: they
are the evaluation set.
"""

import argparse
import gzip
import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kff.read.digits import CELL_PX, CLASSES, MODEL, features, normalize  # noqa: E402

FONTS = ROOT / "src" / "kff" / "render" / "fonts"


def load_idx(path: Path) -> np.ndarray:
    with gzip.open(path) as f:
        data = f.read()
    if data[2] == 8 and data[3] == 3:
        n, rows, cols = (int.from_bytes(data[i : i + 4], "big") for i in (4, 8, 12))
        return np.frombuffer(data, np.uint8, offset=16).reshape(n, rows, cols)
    n = int.from_bytes(data[4:8], "big")
    return np.frombuffer(data, np.uint8, offset=8)[:n]


def into_cell(glyph: np.ndarray, rng: random.Random) -> np.ndarray:
    """A white-on-black glyph → a grey cell crop like a scan: dark ink on paper, sized
    and placed as people write in a box."""
    w, h = CELL_PX
    ys, xs = np.nonzero(glyph > 30)
    if len(ys) == 0:
        return np.full((h, w), 245, np.uint8)
    glyph = glyph[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    gh, gw = glyph.shape
    target = rng.uniform(0.5, 0.9) * h
    if gh < gw * 0.4:  # a dot or dash: keep it small
        target = rng.uniform(0.12, 0.3) * h
    scale = min(target / gh, (w * 0.92) / gw)
    glyph = cv2.resize(
        glyph, (max(1, round(gw * scale)), max(1, round(gh * scale))), interpolation=cv2.INTER_AREA
    )
    gh, gw = glyph.shape
    canvas = np.zeros((h, w), np.uint8)
    top = rng.randint(0, max(0, h - gh))
    left = rng.randint(0, max(0, w - gw))
    canvas[top : top + gh, left : left + gw] = glyph
    paper = rng.randint(215, 250)
    ink = rng.randint(10, 90)
    cell = (paper - canvas.astype(np.float32) / 255 * (paper - ink)).astype(np.float32)
    cell += np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, rng.uniform(1, 6), cell.shape)
    if rng.random() < 0.5:
        cell = cv2.GaussianBlur(cell, (0, 0), rng.uniform(0.3, 0.9))
    return np.clip(cell, 0, 255).astype(np.uint8)


def augment(glyph: np.ndarray, rng: random.Random, strength: float = 1.0) -> np.ndarray:
    h, w = glyph.shape
    pad = max(h, w) // 2
    g = cv2.copyMakeBorder(glyph, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    gh, gw = g.shape
    angle = rng.uniform(-12, 12) * strength
    shear = rng.uniform(-0.3, 0.3) * strength
    m = cv2.getRotationMatrix2D((gw / 2, gh / 2), angle, rng.uniform(0.9, 1.1))
    m[0, 1] += shear
    g = cv2.warpAffine(g, m, (gw, gh), borderValue=0)
    if rng.random() < 0.5:
        k = np.ones((2, 2), np.uint8)
        g = cv2.dilate(g, k) if rng.random() < 0.6 else cv2.erode(g, k)
    if rng.random() < 0.5 * strength:  # elastic wobble, like a hand
        dx = (
            cv2.GaussianBlur(
                np.random.default_rng(rng.randint(0, 1 << 30))
                .uniform(-1, 1, g.shape)
                .astype(np.float32),
                (0, 0),
                4,
            )
            * 6
        )
        dy = (
            cv2.GaussianBlur(
                np.random.default_rng(rng.randint(0, 1 << 30))
                .uniform(-1, 1, g.shape)
                .astype(np.float32),
                (0, 0),
                4,
            )
            * 6
        )
        xs, ys = np.meshgrid(np.arange(gw, dtype=np.float32), np.arange(gh, dtype=np.float32))
        g = cv2.remap(g, xs + dx, ys + dy, cv2.INTER_LINEAR, borderValue=0)
    return g


def render_glyph(ch: str, font: Path, size: int = 96) -> np.ndarray:
    img = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(img).text(
        (size // 2, size // 4), ch, fill=255, font=ImageFont.truetype(str(font), size)
    )
    return np.array(img)


def sample(cell: np.ndarray) -> np.ndarray | None:
    norm = normalize(cell)
    return None if norm is None else features(*norm)


def build(mnist: Path, per_font_class: int, rng: random.Random, limit: int | None):
    X, y = [], []
    images = load_idx(mnist / "train-images-idx3-ubyte.gz")
    labels = load_idx(mnist / "train-labels-idx1-ubyte.gz")
    order = list(range(len(images)))[: limit or len(images)]
    for i in order:
        g = augment(images[i], rng, 0.6) if rng.random() < 0.6 else images[i]
        f = sample(into_cell(g, rng))
        if f is not None:
            X.append(f)
            y.append(int(labels[i]))
    fonts = {
        "latin": [FONTS / "NotoSans-Regular.ttf", FONTS / "NotoSans-Bold.ttf"],
        "arabic": [FONTS / "NotoNaskhArabic-Regular.ttf", FONTS / "NotoNaskhArabic-Bold.ttf"],
    }
    for cls, ch in enumerate(CLASSES):
        script = "latin" if cls < 10 else "arabic"
        n = per_font_class // (4 if script == "latin" else 1)
        for font in fonts[script]:
            base = render_glyph(ch, font)
            for _ in range(n):
                f = sample(into_cell(augment(base, rng, 1.3), rng))
                if f is not None:
                    X.append(f)
                    y.append(cls)
    return np.stack(X), np.array(y)


def train(
    X: np.ndarray,
    y: np.ndarray,
    Xv: np.ndarray,
    yv: np.ndarray,
    hidden: int,
    epochs: int,
    seed: int = 1,
) -> dict[str, np.ndarray]:
    """features → hidden ReLU → softmax, by mini-batch Adam with weight decay; keeps the
    weights that did best on the validation set."""
    rng = np.random.default_rng(seed)
    n_in, n_out = X.shape[1], len(CLASSES)
    w = {
        "w1": (rng.standard_normal((n_in, hidden)) * np.sqrt(2 / n_in)).astype(np.float32),
        "b1": np.zeros(hidden, np.float32),
        "w2": (rng.standard_normal((hidden, n_out)) * np.sqrt(1 / hidden)).astype(np.float32),
        "b2": np.zeros(n_out, np.float32),
    }
    m = {k: np.zeros_like(v) for k, v in w.items()}
    v2 = {k: np.zeros_like(v) for k, v in w.items()}
    lr, b1, b2, eps, decay, batch, step = 1e-3, 0.9, 0.999, 1e-8, 1e-4, 256, 0
    onehot = np.eye(n_out, dtype=np.float32)
    best, best_acc = None, -1.0
    for epoch in range(epochs):
        order = rng.permutation(len(y))
        for i in range(0, len(y), batch):
            idx = order[i : i + batch]
            xb, tb = X[idx], onehot[y[idx]] * 0.95 + 0.05 / n_out  # light label smoothing
            h_pre = xb @ w["w1"] + w["b1"]
            h = np.maximum(h_pre, 0)
            logits = h @ w["w2"] + w["b2"]
            p = np.exp(logits - logits.max(axis=1, keepdims=True))
            p /= p.sum(axis=1, keepdims=True)
            d_logits = (p - tb) / len(idx)
            grads = {
                "w2": h.T @ d_logits + decay * w["w2"],
                "b2": d_logits.sum(axis=0),
            }
            d_h = (d_logits @ w["w2"].T) * (h_pre > 0)
            grads["w1"] = xb.T @ d_h + decay * w["w1"]
            grads["b1"] = d_h.sum(axis=0)
            step += 1
            for k in w:
                m[k] = b1 * m[k] + (1 - b1) * grads[k]
                v2[k] = b2 * v2[k] + (1 - b2) * grads[k] ** 2
                mh = m[k] / (1 - b1**step)
                vh = v2[k] / (1 - b2**step)
                w[k] -= (lr * mh / (np.sqrt(vh) + eps)).astype(np.float32)
        acc = accuracy(w, Xv, yv)
        print(f"epoch {epoch + 1}: validation {acc:.4f}", flush=True)
        if acc > best_acc:
            best, best_acc = {k: v.copy() for k, v in w.items()}, acc
        lr *= 0.85
    assert best is not None
    return best


def accuracy(w: dict[str, np.ndarray], X: np.ndarray, y: np.ndarray) -> float:
    h = np.maximum(X @ w["w1"] + w["b1"], 0)
    return float(np.mean(np.argmax(h @ w["w2"] + w["b2"], axis=1) == y))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mnist", type=Path, default=Path.home() / ".cache" / "kff" / "mnist")
    ap.add_argument("--per-font-class", type=int, default=1500)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--limit", type=int, help="MNIST samples to use (default: all)")
    ap.add_argument("--rebuild", action="store_true", help="ignore the cached dataset")
    args = ap.parse_args()
    rng = random.Random(1)
    np.random.seed(1)

    cache = args.mnist.parent / f"digits-dataset-{args.limit or 'all'}-{args.per_font_class}.npz"
    if cache.exists() and not args.rebuild:
        with np.load(cache) as d:
            X, y, test_x, test_y = d["X"], d["y"], d["test_x"], d["test_y"]
    else:
        X, y = build(args.mnist, args.per_font_class, rng, args.limit)
        tx, ty = [], []
        images = load_idx(args.mnist / "t10k-images-idx3-ubyte.gz")
        labels = load_idx(args.mnist / "t10k-labels-idx1-ubyte.gz")
        for img, lab in zip(images[:5000], labels[:5000], strict=True):
            f = sample(into_cell(img, rng))
            if f is not None:
                tx.append(f)
                ty.append(int(lab))
        test_x, test_y = np.stack(tx), np.array(ty)
        np.savez_compressed(cache, X=X, y=y, test_x=test_x, test_y=test_y)
    print(f"{len(y)} samples, {X.shape[1]} features", flush=True)

    idx = np.random.permutation(len(y))
    X, y = X[idx], y[idx]
    split = int(len(y) * 0.95)
    # Standardise: HOG values are small next to pixel values.
    mean = X[:split].mean(axis=0)
    std = X[:split].std(axis=0) + 1e-6
    Z = ((X - mean) / std).astype(np.float32)
    weights = train(Z[:split], y[:split], Z[split:], y[split:], args.hidden, args.epochs)
    print(f"held out (same sources): {accuracy(weights, Z[split:], y[split:]):.4f}")
    zt = ((test_x - mean) / std).astype(np.float32)
    print(f"MNIST test set: {accuracy(weights, zt, test_y):.4f}")

    MODEL.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(MODEL, mean=mean.astype(np.float32), std=std.astype(np.float32), **weights)
    print(f"saved {MODEL} ({MODEL.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
