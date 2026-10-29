"""Find a page's corner markers and QR code, and map it back onto the printed layout.

The four ArUco markers (ids 0–3 clockwise from top-left) give 16 point pairs between the
scan and the page in mm; a homography from those undoes rotation (any angle, upside down
included), scale and the perspective of a phone photo. The QR code names the layout, so
the markers' printed positions come from the layout map, not from assumptions.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import zxingcpp

from kff.errors import KffError
from kff.render.options import PAPERS, Geometry
from kff.render.pdf import MARKER_DICT, QR_PREFIX

# Registered pages are resampled at this resolution (8 px/mm ≈ 203 dpi).
PX_PER_MM = 8.0
# Mean marker-corner error above this means a bad registration (crumpled page, wrong
# marker); the scan is refused rather than read from the wrong places.
MAX_RESIDUAL_MM = 1.0


@dataclass(frozen=True)
class QrRef:
    layout_hash: str
    sheet_id: str
    page: int
    pages: int


_PAYLOAD = re.compile(r"^([0-9A-F]{8}):([0-9A-F]{32}):(\d{1,3})/(\d{1,3})$")


def parse_payload(text: str) -> QrRef:
    prefix, _, rest = text.strip().partition(":")
    m = _PAYLOAD.match(rest)
    if prefix != QR_PREFIX or not m:
        raise KffError(f"The QR code isn't a kff page code: {text[:60]!r}")
    page, pages = int(m.group(3)), int(m.group(4))
    if not 1 <= page <= pages:
        raise KffError(f"The QR code has a bad page number: {page}/{pages}")
    return QrRef(m.group(1).lower(), m.group(2).lower(), page, pages)


@dataclass
class Registered:
    ref: QrRef
    layout: dict[str, Any]
    # Scan pixels → page mm.
    homography: np.ndarray
    residual_mm: float
    markers: list[int]
    # The page resampled upright at PX_PER_MM.
    image: np.ndarray

    def to_px(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        return round(x_mm * PX_PER_MM), round(y_mm * PX_PER_MM)


def detect_markers(gray: np.ndarray) -> dict[int, np.ndarray]:
    """Marker id → its 4 corners in scan pixels (clockwise from the marker's top-left)."""
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(MARKER_DICT), cv2.aruco.DetectorParameters()
    )
    corners, ids, _ = detector.detectMarkers(gray)
    found: dict[int, np.ndarray] = {}
    if ids is None:
        return found
    for marker_id, c in zip(ids.flatten(), corners, strict=True):
        if int(marker_id) in (0, 1, 2, 3) and int(marker_id) not in found:
            found[int(marker_id)] = c[0].astype(np.float64)
    return found


def _marker_mm(marker: dict[str, float]) -> np.ndarray:
    x, y, w, h = marker["x"], marker["y"], marker["w"], marker["h"]
    return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float64)


def _homography(
    found: dict[int, np.ndarray], markers_mm: dict[int, np.ndarray]
) -> tuple[np.ndarray, float]:
    ids = [i for i in sorted(found) if i in markers_mm]
    if len(ids) < 3:
        raise KffError(
            f"Only {len(ids)} of the 4 corner markers were found; rescan with the whole page "
            "visible and flat."
        )
    src = np.concatenate([found[i] for i in ids])
    dst = np.concatenate([markers_mm[i] for i in ids])
    h, _ = cv2.findHomography(src, dst, 0)
    if h is None:
        raise KffError("Couldn't map the corner markers onto the page.")
    projected = cv2.perspectiveTransform(src.reshape(-1, 1, 2), h).reshape(-1, 2)
    return h, float(np.mean(np.linalg.norm(projected - dst, axis=1)))


def decode_qr(gray: np.ndarray, found: dict[int, np.ndarray]) -> str:
    """The page's QR text: read from the scan as it is, else from upright guesses based on
    the markers (for small or skewed scans)."""
    text = _read_qr(gray)
    if text:
        return text
    geo = Geometry()
    for paper in _candidate_papers(found):
        w, h = paper
        markers_mm = _geometry_markers(geo, w, h)
        try:
            hom, residual = _homography(found, markers_mm)
        except KffError:
            continue
        if residual > 3:
            continue
        # Upright, at high resolution, just the QR's corner of the page.
        scale = 12.0
        qx = w - geo.marker_offset - geo.marker_size - geo.qr_gap - geo.qr_size
        box = (qx - 4, geo.marker_offset - 4, geo.qr_size + 8, geo.qr_size + 8)
        shift = np.array([[scale, 0, -box[0] * scale], [0, scale, -box[1] * scale], [0, 0, 1]])
        size = (round(box[2] * scale), round(box[3] * scale))
        crop = cv2.warpPerspective(gray, shift @ hom, size, flags=cv2.INTER_CUBIC, borderValue=255)
        text = _read_qr(crop)
        if text:
            return text
    raise KffError("No readable QR code on this page; rescan it, or check it's a kff sheet.")


def _read_qr(gray: np.ndarray) -> str | None:
    """zxing-cpp first (it reads blurrier, smaller codes than OpenCV), then OpenCV."""
    for result in zxingcpp.read_barcodes(gray, formats=zxingcpp.BarcodeFormat.QRCode):
        if result.text.startswith(QR_PREFIX + ":"):
            return result.text
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(gray)
    return text or None


def _geometry_markers(geo: Geometry, w: float, h: float) -> dict[int, np.ndarray]:
    o, s = geo.marker_offset, geo.marker_size
    spots = {0: (o, o), 1: (w - o - s, o), 2: (w - o - s, h - o - s), 3: (o, h - o - s)}
    return {i: _marker_mm({"x": x, "y": y, "w": s, "h": s}) for i, (x, y) in spots.items()}


def _candidate_papers(found: dict[int, np.ndarray]) -> list[tuple[float, float]]:
    """Paper sizes (portrait and landscape), closest aspect ratio first."""
    papers = [p for w, h in PAPERS.values() for p in ((w, h), (h, w))]
    if not all(i in found for i in (0, 1, 3)):
        return papers
    centre = {i: c.mean(axis=0) for i, c in found.items()}
    across = np.linalg.norm(centre[1] - centre[0])
    down = np.linalg.norm(centre[3] - centre[0])
    ratio = across / down if down else 1
    geo = Geometry()
    inset = 2 * geo.marker_offset + geo.marker_size
    return sorted(papers, key=lambda p: abs((p[0] - inset) / (p[1] - inset) - ratio))


def register(gray: np.ndarray, get_layout: Callable[[str], dict[str, Any] | None]) -> Registered:
    """Register one page. `get_layout(layout_hash)` looks up the layout map (the store)."""
    if gray.ndim != 2:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    found = detect_markers(gray)
    ref = parse_payload(decode_qr(gray, found))
    layout = get_layout(ref.layout_hash)
    if layout is None:
        raise KffError(
            f"Layout {ref.layout_hash} isn't in this store: render the "
            "sheets here, or copy the store they were rendered in."
        )
    if ref.pages != layout["pages_per_sheet"] or ref.page > ref.pages:
        raise KffError(f"Page {ref.page} is outside the layout's {layout['pages_per_sheet']}.")

    markers_mm = {m["id"]: _marker_mm(m) for m in layout["markers"]}
    hom, residual = _homography(found, markers_mm)
    if residual > MAX_RESIDUAL_MM:
        raise KffError(
            f"The page doesn't sit flat on the markers (error {residual:.1f} mm); rescan it "
            "flatter."
        )
    w, h = layout["paper"]["w"], layout["paper"]["h"]
    scale = np.diag([PX_PER_MM, PX_PER_MM, 1.0])
    upright = cv2.warpPerspective(
        gray,
        scale @ hom,
        (round(w * PX_PER_MM), round(h * PX_PER_MM)),
        flags=cv2.INTER_LINEAR,
        borderValue=255,
    )
    return Registered(ref, layout, hom, residual, sorted(found), upright)
