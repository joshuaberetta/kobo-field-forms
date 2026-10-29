"""Make a clean page raster look scanned or photographed: warp, blur, noise, JPEG.

Returns the 3×3 matrix applied, so tests can measure registration error exactly.
"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Degrade:
    rotate_deg: float = 0.0
    scale: float = 1.0
    shift_px: tuple[float, float] = (0.0, 0.0)
    # Corner displacement as a fraction of the page size (phone-photo perspective).
    perspective: float = 0.0
    upside_down: bool = False
    blur: float = 0.0  # Gaussian sigma in px
    noise: float = 0.0  # Gaussian noise sigma, in grey levels
    jpeg_quality: int | None = None
    # Extra white border, as a scanner bed or table around the page.
    pad_px: int = 0
    seed: int = 0


def degrade(img: np.ndarray, d: Degrade) -> tuple[np.ndarray, np.ndarray]:
    """(degraded image, 3×3 matrix from original to degraded pixels)."""
    rng = np.random.default_rng(d.seed)
    h, w = img.shape[:2]
    out_w, out_h = w + 2 * d.pad_px, h + 2 * d.pad_px
    centre = np.array([w / 2, h / 2])

    rot = cv2.getRotationMatrix2D(
        tuple(centre), d.rotate_deg + (180 if d.upside_down else 0), d.scale
    )
    affine = np.vstack([rot, [0, 0, 1]])
    move = np.array([[1, 0, d.pad_px + d.shift_px[0]], [0, 1, d.pad_px + d.shift_px[1]], [0, 0, 1]])
    m = move @ affine
    if d.perspective:
        src = np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]])
        jitter = rng.uniform(-1, 1, (4, 2)) * d.perspective * np.array([out_w, out_h])
        persp = cv2.getPerspectiveTransform(src, np.float32(src + jitter))
        m = persp @ m
    out = cv2.warpPerspective(img, m, (out_w, out_h), flags=cv2.INTER_LINEAR, borderValue=255)

    if d.blur:
        out = cv2.GaussianBlur(out, (0, 0), d.blur)
    if d.noise:
        out = np.clip(out + rng.normal(0, d.noise, out.shape), 0, 255).astype(np.uint8)
    if d.jpeg_quality:
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, d.jpeg_quality])
        assert ok
        out = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    return out, m
