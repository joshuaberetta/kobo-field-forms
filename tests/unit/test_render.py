"""Render the fixture form and check what the scanner will rely on: the markers and QR
codes can be found in a raster of the page, and the layout map's boxes are where they're
drawn. Set KFF_UPDATE_GOLDENS=1 to rewrite the golden layout after a deliberate change."""

import json
import os
import re
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import pytest

from kff.errors import KffError
from kff.render.options import RenderOptions
from kff.render.pdf import qr_payload, render, render_png
from kff.store import FormVersion

FIXTURES = Path(__file__).parents[1] / "fixtures"
GOLDEN = FIXTURES / "golden" / "all_types.layout.json"
SHEET_IDS = ["0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"]
PPI = 200
PX = PPI / 25.4


@pytest.fixture(scope="module")
def form():
    return FormVersion(
        server="kf.kobotoolbox.org",
        asset_uid="aAllTypes000000000000",
        version_uid="vAllTypes000000000000",
        name="All question types",
        version_number="3",
        date_deployed="2026-10-01T00:00:00Z",
        deployment_uuid="uuid",
        content_hash="h",
        content=json.loads((FIXTURES / "forms" / "all_types.json").read_text()),
    )


@pytest.fixture(scope="module")
def rendered(form):
    return render(form, copies=2, sheet_ids=SHEET_IDS)


@pytest.fixture(scope="module")
def pages(rendered, form):
    return [
        cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
        for png in render_png(rendered, form, PPI)
    ]


def pdf_pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", pdf))


def test_pdf_has_every_sheet(rendered):
    assert rendered.pdf.startswith(b"%PDF")
    assert rendered.pages_per_sheet == 2
    assert pdf_pages(rendered.pdf) == 2 * len(SHEET_IDS)
    assert [s.code for s in rendered.sheets] == ["0123-4567", "FEDC-BA98"]
    assert rendered.sheets[0].instance_id == "uuid:01234567-89ab-cdef-0123-456789abcdef"


def test_layout_hash_is_stable_and_tracks_options(form, rendered):
    assert render(form, sheet_ids=SHEET_IDS[:1]).layout_hash == rendered.layout_hash
    assert render(form, RenderOptions(paper="us-letter")).layout_hash != rendered.layout_hash
    assert render(form, RenderOptions(language="ar")).layout_hash != rendered.layout_hash


def test_every_field_has_its_marks(rendered):
    layout = rendered.layout
    marks = Counter((m["f"], m["k"]) for m in layout["marks"])
    for fid, info in layout["fields"].items():
        if info["widget"] == "choices":
            assert marks[(fid, "choice")] == len(info["choices"]), fid
        elif info["widget"] == "comb":
            assert marks[(fid, "cell")] == sum(s["cells"] for s in info["segments"]), fid
        else:
            assert marks[(fid, "area")] == 1, fid
    assert {m["f"] for m in layout["marks"]} == set(layout["fields"])


def test_marks_are_inside_the_printable_body(rendered):
    layout = rendered.layout
    g, paper = layout["geometry"], layout["paper"]
    for m in layout["marks"]:
        assert 1 <= m["page"] <= layout["pages_per_sheet"]
        assert (
            g["margin_x"] - 0.01 <= m["x"] and m["x"] + m["w"] <= paper["w"] - g["margin_x"] + 0.01
        )
        assert (
            g["margin_top"] - 0.01 <= m["y"] and m["y"] + m["h"] <= paper["h"] - g["margin_bottom"]
        )


def test_markers_found_where_the_layout_says(rendered, pages):
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    for img in pages:
        corners, ids, _ = detector.detectMarkers(img)
        found = {int(i): c[0][0] / PX for i, c in zip(ids.flatten(), corners, strict=True)}
        assert sorted(found) == [0, 1, 2, 3]
        for m in rendered.layout["markers"]:
            assert np.hypot(*(found[m["id"]] - (m["x"], m["y"]))) < 0.3


def test_qr_codes_decode_to_their_page(rendered, pages):
    detector = cv2.QRCodeDetector()
    for p, img in enumerate(pages, 1):
        text, _, _ = detector.detectAndDecode(img)
        assert text == qr_payload(rendered.layout_hash, SHEET_IDS[0], p, 2)


def test_boxes_are_drawn_where_the_layout_says(rendered, pages):
    """The top edge of every box and bubble is dark exactly at the mapped position."""
    for m in rendered.layout["marks"]:
        if m["k"] not in ("cell", "choice"):
            continue
        img = pages[m["page"] - 1]
        y = round(m["y"] * PX)
        x0, x1 = round((m["x"] + 0.35 * m["w"]) * PX), round((m["x"] + 0.65 * m["w"]) * PX)
        assert img[y - 1 : y + 2, x0:x1].min() < 150, m
        # ...and the inside is blank.
        cy = round((m["y"] + m["h"] / 2) * PX)
        assert img[cy - 2 : cy + 3, x0:x1].min() > 200, m


def test_golden_layout(rendered):
    layout = {k: v for k, v in rendered.layout.items() if k != "layout_hash"}
    if os.environ.get("KFF_UPDATE_GOLDENS"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(layout, indent=1, ensure_ascii=False) + "\n")
    assert layout == json.loads(GOLDEN.read_text())


def test_render_rejects_bad_input(form):
    with pytest.raises(KffError, match="copies"):
        render(form, copies=0)
    with pytest.raises(KffError, match="no language 'fr'"):
        render(form, RenderOptions(language="fr"))
    with pytest.raises(KffError, match="Unknown paper"):
        RenderOptions(paper="b5")


def test_arabic_pages_mirror_but_marks_stay_true(form):
    """With `lang: ar` Typst lays the page out right to left; the map must follow it,
    and digit boxes must still run left to right."""
    rendered = render(form, RenderOptions(language="ar"))
    pages = [
        cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
        for png in render_png(rendered, form, PPI)
    ]
    for m in rendered.layout["marks"]:
        if m["k"] not in ("cell", "choice"):
            continue
        img = pages[m["page"] - 1]
        y = round(m["y"] * PX)
        x0, x1 = round((m["x"] + 0.35 * m["w"]) * PX), round((m["x"] + 0.65 * m["w"]) * PX)
        assert img[y - 1 : y + 2, x0:x1].min() < 150, m
    age = sorted((m for m in rendered.layout["marks"] if m["f"] == "age"), key=lambda m: m["i"])
    assert [m["x"] for m in age] == sorted(m["x"] for m in age)
    # Right to left: the age boxes sit at the right of the page.
    assert age[0]["x"] > rendered.layout["paper"]["w"] / 2
