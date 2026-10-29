"""Render a form version to a PDF of printable sheets, plus its layout map.

Two passes over the same template:
1. One placeholder sheet, to count its pages and read every answer area's position
   (`typst query "<mark>"`). That fixes the layout hash and the QR payloads.
2. All the sheets, each page with its own QR code. The QR code and title sit in the page
   foreground, so they can't move the body: every sheet's layout equals the first pass's,
   which is checked.
"""

import hashlib
import io
import json
import uuid
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any

import cv2
import pypdfium2 as pdfium
import segno
import typst

from kff.errors import KffError
from kff.form.model import ModelOptions, PrintModel, build_model
from kff.render.options import Geometry, RenderOptions
from kff.render.strings import strings_for
from kff.scripts import DEFAULT as DEFAULT_SCRIPT
from kff.scripts import scripts
from kff.store import FormVersion

HERE = Path(__file__).parent
TEMPLATE = HERE / "form.typ"
FONTS = HERE / "fonts"
QR_PREFIX = "KFF1"
# Marker ids by corner: top-left, top-right, bottom-right, bottom-left.
MARKER_DICT = cv2.aruco.DICT_4X4_50
MARKER_IDS = (0, 1, 2, 3)
LAYOUT_FORMAT = 1


@dataclass(frozen=True)
class Sheet:
    id: str  # uuid4 hex; the submission's instanceID is `uuid:<dashed form>`
    copy: int

    @property
    def code(self) -> str:
        """Short code printed for people: `4F3A-91C2`."""
        return f"{self.id[:4]}-{self.id[4:8]}".upper()

    @property
    def instance_id(self) -> str:
        return f"uuid:{uuid.UUID(self.id)}"


@dataclass
class Rendered:
    pdf: bytes
    layout: dict[str, Any]
    sheets: list[Sheet]
    model: PrintModel
    # What the template was given (minus the sheets), for previews.
    data: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def layout_hash(self) -> str:
        return self.layout["layout_hash"]

    @property
    def pages_per_sheet(self) -> int:
        return self.layout["pages_per_sheet"]


def qr_payload(layout_hash: str, sheet_id: str, page: int, pages: int) -> str:
    """`KFF1:<LAYOUT>:<SHEET>:<page>/<pages>`, in QR alphanumeric characters only (digits,
    capitals, `:` and `/`), which pack 5.5 bits each instead of 8. The layout hash finds the
    form version in the store; the footer prints the form's uids for people."""
    return f"{QR_PREFIX}:{layout_hash.upper()}:{sheet_id.upper()}:{page}/{pages}"


def _qr_svg(payload: str) -> str:
    # Level Q survives ~25% damage; the page around it is the quiet zone, plus 2 modules.
    buf = io.BytesIO()
    segno.make(payload, error="q", micro=False, boost_error=False).save(
        buf, kind="svg", border=2, xmldecl=False, svgns=True
    )
    return buf.getvalue().decode()


@cache
def marker_bits() -> list[list[list[int]]]:
    """Each corner marker as rows of 1 (white) / 0 (black), border included."""
    dictionary = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    return [(cv2.aruco.generateImageMarker(dictionary, i, 6) // 255).tolist() for i in MARKER_IDS]


def _compiler(data: dict[str, Any]) -> typst.Compiler:
    return typst.Compiler(
        TEMPLATE,
        root=HERE,
        font_paths=[FONTS],
        ignore_system_fonts=True,
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
    )


def _query(compiler: typst.Compiler, label: str) -> list[dict[str, Any]]:
    return json.loads(compiler.query(label, field="value"))


def _round(item: dict[str, Any]) -> dict[str, Any]:
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in item.items()}


def _version_label(form: FormVersion) -> str:
    return f"v{form.version_number or '?'} ({(form.date_deployed or '')[:10]})"


def _fonts(model: PrintModel) -> list[str]:
    """Latin first (digits, codes, footer), then each sheet language's script."""
    fonts = list(scripts()[DEFAULT_SCRIPT].fonts)
    for lang in model.languages:
        fonts += [f for f in scripts()[lang.script].fonts if f not in fonts]
    return fonts


def _instructions(model: PrintModel) -> list[dict[str, Any]]:
    out = []
    for lang in model.languages:
        strings, _ = strings_for(lang.code)
        text = strings["instructions"]
        if not any(o["text"] == text for o in out):
            out.append(
                {"text": text, "required": strings["required"], "lang": lang.code, "dir": lang.dir}
            )
    return out


def render(
    form: FormVersion,
    options: RenderOptions | None = None,
    *,
    copies: int = 1,
    sheet_ids: list[str] | None = None,
) -> Rendered:
    """`copies` sheets of `form` in one layout. `sheet_ids` fixes the ids (tests); default:
    random. For `per-sheet` languages use `render_set`."""
    options = options or RenderOptions()
    if not 1 <= copies <= 500:
        raise KffError("copies must be between 1 and 500.")
    geometry = Geometry()
    geometry.check(options)
    try:
        model = build_model(
            form.content,
            form.name,
            ModelOptions(
                languages=options.languages,
                repeat_rows=options.repeat_rows,
                text_cells=geometry.text_cells(options),
                text_entry=options.text_entry,
                choice_layout=options.choice_layout,
            ),
        )
    except ValueError as err:
        raise KffError(str(err)) from None

    page_lang = model.languages[0]
    width, height = options.size
    base: dict[str, Any] = {
        "options": options.to_json(),
        "geometry": geometry.to_json(),
        "paper": {"w": width, "h": height},
        "model": model.to_json(),
        "form": {
            "server": form.server,
            "asset_uid": form.asset_uid,
            "version_uid": form.version_uid,
            "version_label": _version_label(form),
        },
        "markers": marker_bits(),
        "lang": page_lang.code,
        "dir": page_lang.dir,
        "fonts": _fonts(model),
        "strings": strings_for(page_lang.code)[0],
        "instructions": _instructions(model),
    }
    template_hash = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    layout_hash = hashlib.sha256(
        json.dumps({"template": template_hash, **base}, sort_keys=True).encode()
    ).hexdigest()[:8]
    base["layout_hash"] = layout_hash

    # Pass 1: one placeholder sheet.
    first = _compiler(
        {**base, "pages_per_sheet": 0, "pad_blank": False, "sheets": [{"code": "----", "qr": []}]}
    )
    try:
        marks = [_round(m) for m in _query(first, "<mark>")]
        page_marks = [_round(m) for m in _query(first, "<page-mark>")]
    except typst.TypstError as err:
        raise KffError(f"Typst failed to lay out the form: {err.message}") from None
    content_pages = sum(1 for m in page_marks if m["k"] == "page")
    _check_fit(marks, model, geometry, height)
    # Double-sided: pad each sheet to an even page count, so no leaf holds two sheets.
    pad_blank = options.sides == "double" and content_pages % 2 == 1
    pages = content_pages + pad_blank
    base["pages_per_sheet"] = pages
    base["pad_blank"] = pad_blank

    ids = sheet_ids or [uuid.uuid4().hex for _ in range(copies)]
    if len(ids) != copies:
        raise KffError("sheet_ids must have one id per copy.")
    sheets = [Sheet(sid, i + 1) for i, sid in enumerate(ids)]

    # Pass 2: every sheet.
    final = _compiler({**base, "sheets": [_sheet_data(s, layout_hash, pages) for s in sheets]})
    try:
        pdf = final.compile(format="pdf")
        all_marks = _query(final, "<mark>")
    except typst.TypstError as err:
        raise KffError(f"Typst failed to render the form: {err.message}") from None
    _check_sheets(marks, all_marks, pages, copies)

    page_one = [m for m in page_marks if m["page"] == 1]
    layout = {
        "format": LAYOUT_FORMAT,
        "layout_hash": layout_hash,
        "form": base["form"],
        "language": model.language,
        "languages": [lang.name for lang in model.languages],
        "options": options.to_json(),
        "geometry": geometry.to_json(),
        "paper": {
            "name": options.paper,
            "orientation": options.orientation,
            "w": width,
            "h": height,
        },
        "pages_per_sheet": pages,
        "blank_pages": [pages] if pad_blank else [],
        # The same on every page.
        "markers": [
            {k: m[k] for k in ("id", "x", "y", "w", "h")} for m in page_one if m["k"] == "marker"
        ],
        "qr": next({k: m[k] for k in ("x", "y", "w", "h")} for m in page_one if m["k"] == "qr"),
        "marks": marks,
        "fields": _fields(model),
    }
    assert isinstance(pdf, bytes)
    return Rendered(pdf=pdf, layout=layout, sheets=sheets, model=model, data=base)


def render_set(
    form: FormVersion, options: RenderOptions | None = None, *, copies: int = 1
) -> list[Rendered]:
    """One `Rendered` per layout: several with `per-sheet` languages (each language its own
    sheets and layout), else one."""
    options = options or RenderOptions()
    if options.language_layout != "per-sheet" or len(options.languages) < 2:
        return [render(form, options, copies=copies)]
    return [
        render(
            form,
            replace(options, languages=(lang,), language_layout="stacked"),
            copies=copies,
        )
        for lang in options.languages
    ]


def merge_pdfs(pdfs: list[bytes]) -> bytes:
    if len(pdfs) == 1:
        return pdfs[0]
    out = pdfium.PdfDocument.new()
    try:
        for data in pdfs:
            doc = pdfium.PdfDocument(data)
            out.import_pages(doc)
            doc.close()
        buf = io.BytesIO()
        out.save(buf)
        return buf.getvalue()
    finally:
        out.close()


def _sheet_data(sheet: Sheet, layout_hash: str, pages: int) -> dict[str, Any]:
    return {
        "code": sheet.code,
        "qr": [_qr_svg(qr_payload(layout_hash, sheet.id, p, pages)) for p in range(1, pages + 1)],
    }


def render_png(rendered: Rendered, ppi: float = 100) -> list[bytes]:
    """PNG pages of the first sheet (a preview; the PDF is what gets printed)."""
    sheet = _sheet_data(rendered.sheets[0], rendered.layout_hash, rendered.pages_per_sheet)
    out = _compiler({**rendered.data, "sheets": [sheet]}).compile(format="png", ppi=ppi)
    return out if isinstance(out, list) else [out]


def _check_fit(
    marks: list[dict[str, Any]], model: PrintModel, geometry: Geometry, height: float
) -> None:
    """A question taller than a page overflows the body; refuse it instead of printing
    boxes over the footer."""
    bottom = height - geometry.margin_bottom + 0.5
    numbers = {q.id: q.number for q in model.questions()}
    for m in marks:
        if m["y"] + m["h"] > bottom:
            raise KffError(
                f"Question {numbers.get(m['f'], m['f'])} doesn't fit on one page; use a "
                "bigger paper size, landscape, or the choice layout 'columns'."
            )


def _check_sheets(
    first: list[dict[str, Any]], every: list[dict[str, Any]], pages: int, copies: int
) -> None:
    """Every sheet must lay out exactly like the first pass's sheet."""
    if len(every) != len(first) * copies:
        raise KffError("The sheets didn't lay out identically; please report this form.")
    for i, mark in enumerate(every):
        ref = first[i % len(first)]
        sheet = i // len(first)
        moved = abs(mark["x"] - ref["x"]) > 0.01 or abs(mark["y"] - ref["y"]) > 0.01
        if mark["page"] != ref["page"] + sheet * pages or moved:
            raise KffError("The sheets didn't lay out identically; please report this form.")


def _fields(model: PrintModel) -> dict[str, Any]:
    """What the scanner needs about each answer: type, xpath, and the widget's shape."""
    fields: dict[str, Any] = {}
    for q in model.questions():
        w = q.widget
        if w.kind == "none":
            continue
        info: dict[str, Any] = {
            "xpath": q.xpath,
            "type": q.type,
            "number": q.number,
            "widget": w.kind,
            "required": q.required,
        }
        if w.kind == "choices":
            info["multiple"] = w.multiple
            info["choices"] = [c.value for c in w.choices]
        elif w.kind == "comb":
            info["segments"] = [
                {"s": ri * 100 + si, "cells": s.cells, "charset": s.charset, "sep": s.sep}
                for ri, row in enumerate(w.rows)
                for si, s in enumerate(row.segments)
            ]
        fields[q.id] = info
    return fields
