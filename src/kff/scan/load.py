"""Scanned input → grayscale page images. PDFs (a scanner's usual output) are rasterised;
images (PNG, JPEG, TIFF, phone photos) are read as they are."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

from kff.errors import KffError

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
# Plenty for 0.4 mm QR modules and 6 mm boxes; more only costs time.
DEFAULT_DPI = 200
MAX_PIXELS = 60_000_000


@dataclass
class PageImage:
    source: str  # file name, plus `#n` for a page of a PDF or multi-page TIFF
    image: np.ndarray = None  # type: ignore[assignment]  # 2-D uint8, white paper


def load(path: Path, *, data: bytes | None = None, dpi: int = DEFAULT_DPI) -> Iterator[PageImage]:
    """Every page in a file. `data` gives the bytes directly (uploads); `path` then only
    names it."""
    raw = data if data is not None else path.read_bytes()
    name = path.name
    if raw[:5] == b"%PDF-":
        yield from _pdf_pages(raw, name, dpi)
        return
    if path.suffix.lower() not in IMAGE_SUFFIXES and not _looks_like_image(raw):
        raise KffError(f"{name}: not a PDF or an image.")
    ok, pages = cv2.imdecodemulti(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if not ok or not pages:
        raise KffError(f"{name}: can't read this image.")
    for i, img in enumerate(pages, 1):
        if img.size > MAX_PIXELS:
            raise KffError(f"{name}: image too large ({img.shape[1]}×{img.shape[0]}).")
        yield PageImage(f"{name}#{i}" if len(pages) > 1 else name, img)


def _pdf_pages(raw: bytes, name: str, dpi: int) -> Iterator[PageImage]:
    try:
        pdf = pdfium.PdfDocument(raw)
    except pdfium.PdfiumError as err:
        raise KffError(f"{name}: can't open this PDF ({err}).") from None
    try:
        for i in range(len(pdf)):
            bitmap = pdf[i].render(scale=dpi / 72, grayscale=True)
            img = bitmap.to_numpy()
            if img.ndim == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            yield PageImage(f"{name}#{i + 1}", np.ascontiguousarray(img))
    finally:
        pdf.close()


def _looks_like_image(raw: bytes) -> bool:
    return (
        raw[:8] == b"\x89PNG\r\n\x1a\n"
        or raw[:3] == b"\xff\xd8\xff"
        or raw[:4]
        in (
            b"II*\x00",
            b"MM\x00*",
        )
    )
