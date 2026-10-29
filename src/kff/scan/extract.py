"""Cut each answer area out of a registered page, using the layout map."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from kff.scan.register import PX_PER_MM, Registered

# Cells and bubbles are cropped inside their printed outline, so the outline itself
# doesn't read as ink. Areas (lines, boxes) keep a margin for writing that strays out.
INSET_MM = {"cell": 0.7, "choice": 0.8, "area": -1.0}


@dataclass
class Crop:
    mark: dict[str, Any]
    image: np.ndarray


def page_marks(registered: Registered) -> list[dict[str, Any]]:
    return [m for m in registered.layout["marks"] if m["page"] == registered.ref.page]


def crop(registered: Registered, mark: dict[str, Any]) -> np.ndarray:
    inset = INSET_MM.get(mark["k"], 0.0)
    img = registered.image
    x0 = max(0, round((mark["x"] + inset) * PX_PER_MM))
    y0 = max(0, round((mark["y"] + inset) * PX_PER_MM))
    x1 = min(img.shape[1], round((mark["x"] + mark["w"] - inset) * PX_PER_MM))
    y1 = min(img.shape[0], round((mark["y"] + mark["h"] - inset) * PX_PER_MM))
    return img[y0:y1, x0:x1]


def crops(registered: Registered) -> dict[str, list[Crop]]:
    """Field id → its crops on this page, in order (cells by segment and index)."""
    out: dict[str, list[Crop]] = defaultdict(list)
    for mark in page_marks(registered):
        out[mark["f"]].append(Crop(mark, crop(registered, mark)))
    for items in out.values():
        items.sort(key=lambda c: (c.mark.get("s", 0), c.mark.get("i", 0)))
    return dict(out)


def ink(image: np.ndarray) -> float:
    """Share of dark pixels (0–1): the basic OMR signal, calibrated in milestone 5."""
    if image.size == 0:
        return 0.0
    return float(np.mean(image < 128))


def overlay(registered: Registered) -> np.ndarray:
    """The registered page in colour with every mapped answer area outlined, to check by
    eye that the map lands on the boxes."""
    return draw_overlay(registered.image, registered.layout, registered.ref.page)


def draw_overlay(image: np.ndarray, layout: dict[str, Any], page: int) -> np.ndarray:
    """`overlay` for an upright page image (at PX_PER_MM) read back from the store."""
    img = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image.copy()
    colours = {"cell": (0, 140, 255), "choice": (220, 60, 0), "area": (0, 170, 0)}
    for mark in layout["marks"]:
        if mark["page"] != page:
            continue
        x0, y0 = round(mark["x"] * PX_PER_MM), round(mark["y"] * PX_PER_MM)
        x1 = round((mark["x"] + mark["w"]) * PX_PER_MM)
        y1 = round((mark["y"] + mark["h"]) * PX_PER_MM)
        cv2.rectangle(img, (x0, y0), (x1, y1), colours.get(mark["k"], (0, 0, 255)), 2)
    return img
