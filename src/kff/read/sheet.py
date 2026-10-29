"""Read every answer on a scanned sheet: bubbles by OMR, digits by the digit model, text
by the vision model (if configured). Each reading has a value in Kobo's submission
format, a confidence, and a reason when a person should check it."""

import calendar
from dataclasses import asdict, dataclass, field
from typing import Any

import cv2
import numpy as np

from kff.read.digits import ink_mask, read_digits
from kff.read.omr import fill, read_choices
from kff.read.vlm import VlmReader, comb_strip, prompt_for
from kff.scan.extract import Crop, crops_from
from kff.store import Store

REVIEW_BELOW = 0.75
# Inked pixels (at 8 px/mm, ≈ 2 mm² of stroke) that make a lines or box area "written in".
# A count, not a share: a short name on a full-width line covers well under 1% of it.
WRITTEN_PIXELS = 130


@dataclass
class Reading:
    field_id: str
    value: str | None
    confidence: float
    method: str  # omr | digits | vlm | ink | none
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        return self.reason is not None or self.confidence < REVIEW_BELOW

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "needs_review": self.needs_review}


@dataclass
class Context:
    digit_sets: list[str]
    languages: list[str]
    vlm: VlmReader | None = None


def _blank(img: np.ndarray, share: float) -> bool:
    return float((ink_mask(img) > 0.45).mean()) < share


def without_rules(solid: np.ndarray) -> np.ndarray:
    """Ink with the printed straight lines removed: box outlines and ruled lines are long
    and straight (5 mm and more); handwriting isn't."""
    u8 = solid.astype(np.uint8)
    lines = cv2.morphologyEx(u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1)))
    lines |= cv2.morphologyEx(
        u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
    )
    # Widen what was removed a little: a printed line is a few pixels thick when scanned.
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8))
    return u8 & (1 - lines)


def _area_blank(img: np.ndarray) -> bool:
    """No writing in a lines/box area: few inked pixels once the printed lines and specks
    are dropped."""
    solid = without_rules(ink_mask(img) > 0.45)
    n, _, stats, _ = cv2.connectedComponentsWithStats(solid, 8)
    return int(sum(stats[i][4] for i in range(1, n) if stats[i][4] >= 8)) < WRITTEN_PIXELS


def _sign(crop: np.ndarray) -> tuple[str, float]:
    """+ or - in a sign box. A plus is about as tall as it is wide, a minus much wider."""
    solid = ink_mask(crop) > 0.45
    if solid.mean() < 0.012:
        return "+", 0.85  # left empty: most people skip the + sign
    ys, xs = np.nonzero(solid)
    aspect = (ys.max() - ys.min() + 1) / (xs.max() - xs.min() + 1)
    if aspect > 0.6:
        return "+", 0.9
    if aspect < 0.35:
        return "-", 0.9
    return ("+" if aspect >= 0.47 else "-"), 0.5


def _digits_value(cells: list[Any]) -> tuple[str | None, float, str | None]:
    """Cell readings → a digit string (blanks at either end dropped)."""
    values = [c.value for c in cells]
    filled = [i for i, v in enumerate(values) if v is not None]
    if not filled:
        return None, min(c.confidence for c in cells), None
    inner = values[filled[0] : filled[-1] + 1]
    reason = "a gap between digits" if None in inner else None
    conf = min(c.confidence for c in cells)
    return "".join(v or "" for v in inner), conf, reason


def read_field(fid: str, info: dict[str, Any], items: list[Crop], ctx: Context) -> Reading:
    widget, qtype = info["widget"], info["type"]
    if widget == "choices":
        fills = {c.mark["v"]: fill(c.image) for c in items}
        r = read_choices(fills, info.get("multiple", False))
        value = " ".join(r.selected) if r.selected else None
        return Reading(fid, value, r.confidence, "omr", r.reason, {"fills": fills})

    if widget in ("lines", "box"):
        img = items[0].image
        if _area_blank(img):
            return Reading(fid, None, 0.9, "ink")
        if widget == "box":  # a drawing or signature: submitted as an image
            return Reading(
                fid, f"{fid.replace('/', '_')}.png", 0.9, "ink", None, {"attachment": True}
            )
        return _text(fid, info, [img], ctx, boxes=False)

    # Comb: segment by segment.
    segments = {s["s"]: s for s in info["segments"]}
    by_seg: dict[int, list[Crop]] = {}
    for c in items:
        by_seg.setdefault(c.mark["s"], []).append(c)
    if any(s["charset"] == "text" for s in segments.values()):
        cells = [c.image for s in sorted(by_seg) for c in by_seg[s]]
        pattern = box_pattern([_blank(c, 0.012) for c in cells])
        if not pattern:
            return Reading(fid, None, 0.9, "ink")
        return _text(fid, info, [comb_strip(cells)], ctx, boxes=True, pattern=pattern)

    parts: list[tuple[dict[str, Any], str | None]] = []
    confidence = 1.0
    reasons: list[str] = []
    written: list[str] = []
    for sid in sorted(by_seg):
        seg = segments[sid]
        crops = [c.image for c in sorted(by_seg[sid], key=lambda c: c.mark["i"])]
        if seg["charset"] == "sign":
            sign, conf = _sign(crops[0])
            parts.append((seg, sign))
            confidence = min(confidence, conf)
            continue
        cells = read_digits(crops, ctx.digit_sets)
        written += [c.written or "·" for c in cells]
        text, conf, reason = _digits_value(cells)
        parts.append((seg, text))
        confidence = min(confidence, conf)
        if reason:
            reasons.append(reason)
        for c in cells:
            if c.value and c.alternatives and c.alternatives[0][1] > 0.25:
                reasons.append(f"{c.written} could be {c.alternatives[0][0]}")
                break
    value, reason = to_value(qtype, info, parts)
    if reason:
        reasons.append(reason)
    return Reading(
        fid, value, confidence, "digits", "; ".join(reasons) or None, {"written": "".join(written)}
    )


def box_pattern(blank: list[bool]) -> str:
    """Letter boxes as `#` (written in) and ` ` (empty), outer empties dropped:
    `#### ######` is four letters, a gap, six."""
    return "".join(" " if b else "#" for b in blank).strip()


def fit_to_boxes(text: str, pattern: str) -> tuple[str, str | None]:
    """Spaces come from the boxes, not the reader: if it read as many letters as boxes are
    written in, put them in the boxes' words; if not, say so (and keep its text)."""
    letters = [ch for ch in text if not ch.isspace()]
    words = pattern.split()
    need = sum(len(w) for w in words)
    if len(letters) != need:
        return text, f"read {len(letters)} letters, but {need} boxes are written in"
    out, i = [], 0
    for w in words:
        out.append("".join(letters[i : i + len(w)]))
        i += len(w)
    return " ".join(out), None


# The reader's own confidence can only lower trust: in the droplet e2e its wrong readings
# (a dropped letter, a made-up word) came back at 0.9 like its right ones.
UNSURE_BELOW = 0.5
LINES_CONFIDENCE = 0.6


def _text(
    fid: str,
    info: dict[str, Any],
    images: list[np.ndarray],
    ctx: Context,
    boxes: bool,
    pattern: str | None = None,
) -> Reading:
    if ctx.vlm is None:
        return Reading(fid, None, 0.0, "none", "written, but no text reader is configured")
    label = info.get("label") or fid
    r = ctx.vlm.read(images[0], prompt_for(label, ctx.languages, boxes, pattern))
    if not r.text:
        return Reading(fid, None, 0.0, "vlm", "written, but the text reader found nothing")
    detail = {"read": r.text, "reader_confidence": r.confidence}
    if boxes and pattern:
        text, reason = fit_to_boxes(r.text, pattern)
        detail["boxes"] = pattern
        if reason is None and r.confidence < UNSURE_BELOW:
            reason = "the text reader isn't sure"
        return Reading(fid, text, min(r.confidence, 0.9), "vlm", reason, detail)
    # Writing on lines can't be checked against anything: a person always looks.
    return Reading(
        fid,
        r.text,
        min(r.confidence, LINES_CONFIDENCE),
        "vlm",
        "handwriting on lines: check the reading",
        detail,
    )


def to_value(
    qtype: str, info: dict[str, Any], parts: list[tuple[dict[str, Any], str | None]]
) -> tuple[str | None, str | None]:
    """Segment texts → Kobo's submission format, or (None, reason) if it can't be."""
    texts = [t for _, t in parts]
    if all(t in (None, "+") for t in texts):
        return None, None
    try:
        if qtype in ("integer", "range"):
            return str(int(texts[0])), None
        if qtype == "decimal":
            whole, frac = texts[0] or "0", texts[1] or "0"
            return f"{int(whole)}.{frac}", None
        if qtype == "text":
            return texts[0], None
        if qtype in ("date", "datetime"):
            date = _date(texts[:3])
            if qtype == "date":
                return date, None
            return f"{date}T{_time(texts[3:5])}", None
        if qtype == "time":
            return _time(texts[:2]), None
        if qtype == "geopoint":
            lat = _coord(texts[0:3], 90)
            lon = _coord(texts[3:6], 180)
            return f"{lat} {lon} 0 0", None
    except (ValueError, TypeError) as err:
        return None, f"not a valid {qtype}: {err}"
    return None, f"can't read a {qtype} from boxes"


def _date(t: list[str | None]) -> str:
    d, m, y = (int(x) if x else None for x in t)
    if d is None or m is None or y is None:
        raise ValueError("day, month and year are all needed")
    if y < 100:
        y += 2000
    if not (1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1] and 1900 <= y <= 2100):
        raise ValueError(f"{d:02d}/{m:02d}/{y}")
    return f"{y:04d}-{m:02d}-{d:02d}"


def _time(t: list[str | None]) -> str:
    hh, mm = int(t[0] or "x"), int(t[1] or "0")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"{hh:02d}:{mm:02d}")
    return f"{hh:02d}:{mm:02d}:00.000+00:00"


def _coord(t: list[str | None], limit: int) -> str:
    sign, whole, frac = t
    value = float(f"{int(whole or '0')}.{frac or '0'}")
    if value > limit:
        raise ValueError(f"{value} is beyond ±{limit}")
    return f"{'-' if sign == '-' else ''}{value:.5f}"


def read_sheet(store: Store, sheet_id: str, ctx_vlm: VlmReader | None = None) -> list[Reading]:
    """Read a sheet's current scans into the store (reviewed readings are kept)."""
    scans = store.scans(sheet_id)
    if not scans:
        return []
    layout = store.get_layout(scans[0]["layout_hash"])
    if layout is None:
        return []
    ctx = Context(
        layout.get("digit_sets") or ["0123456789"], layout.get("languages") or [], ctx_vlm
    )
    readings: list[Reading] = []
    for scan in scans:
        image = cv2.imread(str(store.path.parent / scan["image"]), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        for fid, items in crops_from(image, layout, scan["page"]).items():
            info = layout["fields"].get(fid)
            if info is not None:
                readings.append(read_field(fid, info, items, ctx))
    store.save_readings(sheet_id, [r.to_json() for r in readings])
    return readings
