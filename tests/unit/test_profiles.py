"""Every print profile end to end: render, degrade like a scan, register, and check the
filled bubble reads as filled and the rest as blank (plan: e2e test matrix)."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from kff.errors import KffError
from kff.render.options import RenderOptions
from kff.render.pdf import merge_pdfs, render, render_png, render_set
from kff.scan.extract import crops, ink
from kff.scan.register import register
from kff.store import FormVersion
from kff.synth.degrade import Degrade, degrade

FIXTURES = Path(__file__).parents[1] / "fixtures"
PPI = 200
PX = PPI / 25.4

PROFILES = {
    "a4 single en": RenderOptions(),
    "letter double en+ar stacked": RenderOptions(
        paper="us-letter", sides="double", languages=("en", "ar")
    ),
    "a4 landscape en+ar columns, 2 columns": RenderOptions(
        orientation="landscape", languages=("en", "ar"), language_layout="columns", columns=2
    ),
    "a5 ar large print": RenderOptions(paper="a5", languages=("ar",), font_size=12),
    "legal ar+en, no hints": RenderOptions(
        paper="us-legal", languages=("ar", "en"), show_hints=False
    ),
}


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


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_renders_and_scans(profile, form):
    options = PROFILES[profile]
    rendered = render(form, options)
    layout = rendered.layout
    w, h = layout["paper"]["w"], layout["paper"]["h"]
    assert (w > h) == (options.orientation == "landscape")
    if options.sides == "double":
        assert layout["pages_per_sheet"] % 2 == 0
    g = layout["geometry"]
    for m in layout["marks"]:
        assert g["margin_x"] - 0.01 <= m["x"] and m["x"] + m["w"] <= w - g["margin_x"] + 0.01
        assert m["y"] + m["h"] <= h - g["margin_bottom"] + 0.5

    page = cv2.imdecode(np.frombuffer(render_png(rendered, PPI)[0], np.uint8), 0)
    yes = next(m for m in layout["marks"] if m["f"] == "consent" and m["v"] == "yes")
    centre = (round((yes["x"] + yes["w"] / 2) * PX), round((yes["y"] + yes["h"] / 2) * PX))
    cv2.circle(page, centre, round(1.7 * PX), 0, -1)
    scan, _ = degrade(
        page,
        Degrade(
            rotate_deg=2, perspective=0.02, blur=0.8, noise=5, jpeg_quality=70, pad_px=60, seed=2
        ),
    )
    reg = register(scan, lambda key: layout if key == rendered.layout_hash else None)
    assert reg.residual_mm < 0.5
    fields = crops(reg)
    consent = {c.mark["v"]: ink(c.image) for c in fields["consent"]}
    assert consent["yes"] > 0.8 and consent["no"] < 0.02
    others = [
        ink(c.image)
        for f, cs in fields.items()
        if f != "consent"
        for c in cs
        if c.mark["k"] != "area"
    ]
    assert max(others) < 0.01


def test_two_question_columns_use_both_halves(form):
    rendered = render(form, RenderOptions(orientation="landscape", columns=2))
    w = rendered.layout["paper"]["w"]
    xs = [m["x"] for m in rendered.layout["marks"] if m["page"] == 1]
    assert min(xs) < w / 2 < max(xs)


def test_double_sided_blank_page(form):
    rendered = render(form, RenderOptions(sides="double", languages=("en", "ar")))
    assert rendered.layout["blank_pages"] == [rendered.pages_per_sheet]
    assert not [m for m in rendered.layout["marks"] if m["page"] in rendered.layout["blank_pages"]]


def test_per_sheet_languages(form):
    options = RenderOptions(languages=("en", "ar"), language_layout="per-sheet")
    rendered = render_set(form, options, copies=2)
    assert [r.model.language for r in rendered] == ["English (en)", "Arabic (ar)"]
    assert len({r.layout_hash for r in rendered}) == 2
    merged = merge_pdfs([r.pdf for r in rendered])
    total = sum(2 * r.pages_per_sheet for r in rendered)
    import pypdfium2 as pdfium

    assert len(pdfium.PdfDocument(merged)) == total


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (dict(paper="a5", columns=2), "question columns"),
        (
            dict(columns=2, languages=("en", "ar"), language_layout="columns"),
            "side by side",
        ),
        (dict(columns=3), "columns must be 1 or 2"),
        (dict(languages=("en", "ar", "en", "ar", "en")), "at most 4"),
        (dict(orientation="sideways"), "orientation"),
    ],
)
def test_impossible_options_refused(form, options, message):
    with pytest.raises(KffError, match=message):
        render(form, RenderOptions(**options))


def test_question_too_tall_for_a_page(form):
    content = dict(form.content)
    content["choices"] = content["choices"] + [
        {"list_name": "big", "name": f"c{i}", "label": [f"A long choice label number {i}"] * 2}
        for i in range(80)
    ]
    content["survey"] = [
        {
            "type": "select_one",
            "select_from_list_name": "big",
            "name": "huge",
            "label": ["Huge"] * 2,
        }
    ]
    tall = FormVersion(**{**form.__dict__, "content": content})
    with pytest.raises(KffError, match="Question 1 doesn't fit on one page"):
        render(tall, RenderOptions(choice_layout="vertical"))
