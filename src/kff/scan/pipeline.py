"""Scan files into the store: load pages, register each, keep the upright page image."""

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from kff.errors import KffError
from kff.render.pdf import Sheet
from kff.scan.load import DEFAULT_DPI, PageImage, load
from kff.scan.register import Registered, register
from kff.store import Store


@dataclass
class PageResult:
    source: str
    registered: Registered | None = None
    scan_id: str | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class SheetStatus:
    sheet_id: str
    pages: int
    found: list[int]

    @property
    def code(self) -> str:
        return Sheet(self.sheet_id, 0).code

    @property
    def missing(self) -> list[int]:
        return [p for p in range(1, self.pages + 1) if p not in self.found]


def scan_page(store: Store, page: PageImage) -> PageResult:
    result = PageResult(page.source)
    try:
        reg = register(page.image, store.get_layout)
    except KffError as err:
        result.error = str(err)
        return result
    result.registered = reg
    sheet_id = reg.ref.sheet_id
    if store.get_sheet(sheet_id) is None:
        result.notes.append(
            "this sheet wasn't printed from this store (another store, or a photocopy: "
            "copies share a sheet id, so only one of them can be submitted)"
        )
    if len(reg.markers) < 4:
        result.notes.append(f"only {len(reg.markers)} corner markers found")

    scan_id = uuid.uuid4().hex
    rel = Path("scans") / sheet_id / f"p{reg.ref.page}-{scan_id[:8]}.png"
    path = store.path.parent / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), reg.image):
        raise KffError(f"Couldn't write {path}.")
    replaced = store.save_scan(
        {
            "scan_id": scan_id,
            "sheet_id": sheet_id,
            "page": reg.ref.page,
            "layout_hash": reg.ref.layout_hash,
            "source": page.source,
            "image": str(rel),
            "residual_mm": round(reg.residual_mm, 3),
            "markers": len(reg.markers),
        }
    )
    if replaced:
        result.notes.append("replaces an earlier scan of this page")
    result.scan_id = scan_id
    return result


def scan_file(
    store: Store, path: Path, *, data: bytes | None = None, dpi: int = DEFAULT_DPI
) -> list[PageResult]:
    try:
        return [scan_page(store, page) for page in load(path, data=data, dpi=dpi)]
    except KffError as err:
        return [PageResult(path.name, error=str(err))]


def sheet_status(store: Store, sheet_ids: list[str]) -> list[SheetStatus]:
    out = []
    for sheet_id in dict.fromkeys(sheet_ids):
        scans = store.scans(sheet_id)
        if not scans:
            continue
        layout = store.get_layout(scans[0]["layout_hash"])
        pages = layout["pages_per_sheet"] if layout else 0
        out.append(SheetStatus(sheet_id, pages, [s["page"] for s in scans]))
    return out
