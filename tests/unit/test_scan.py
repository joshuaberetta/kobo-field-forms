"""Registration on synthetic scans: render the fixture, mark some answers, warp and degrade
the page like a scanner or phone would, then register it and check the field crops."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from kff.cli import main
from kff.errors import KffError
from kff.render.pdf import qr_payload, render, render_png
from kff.scan.extract import crops, ink
from kff.scan.load import load
from kff.scan.pipeline import scan_file, sheet_status
from kff.scan.register import parse_payload, register
from kff.store import FormVersion, Store
from kff.synth.degrade import Degrade, degrade

FIXTURES = Path(__file__).parents[1] / "fixtures"
SHEET = "0123456789abcdef0123456789abcdef"
PPI = 200
PX = PPI / 25.4

CASES = {
    "clean": Degrade(),
    "rotated": Degrade(rotate_deg=3, pad_px=80),
    "skewed": Degrade(rotate_deg=-2, perspective=0.02, pad_px=80, seed=1),
    "upside down": Degrade(upside_down=True, rotate_deg=1, pad_px=60),
    "150 dpi, blurry jpeg": Degrade(
        scale=0.75, blur=1.2, noise=6, jpeg_quality=55, rotate_deg=1.5, pad_px=40
    ),
    "phone photo": Degrade(
        perspective=0.05, rotate_deg=4, blur=0.8, noise=8, jpeg_quality=70, pad_px=150, seed=3
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


@pytest.fixture(scope="module")
def rendered(form):
    return render(form, sheet_ids=[SHEET])


def mark_of(layout, field, **match):
    return next(
        m for m in layout["marks"] if m["f"] == field and all(m[k] == v for k, v in match.items())
    )


@pytest.fixture(scope="module")
def filled_page(rendered):
    """Page 1 with the consent "yes" bubble filled and a stroke in the first age box."""
    img = cv2.imdecode(np.frombuffer(render_png(rendered, PPI)[0], np.uint8), 0)
    yes = mark_of(rendered.layout, "consent", v="yes")
    centre = (round((yes["x"] + yes["w"] / 2) * PX), round((yes["y"] + yes["h"] / 2) * PX))
    cv2.circle(img, centre, round(1.7 * PX), 0, -1)
    age = mark_of(rendered.layout, "age", i=0)
    x, y = (age["x"] + age["w"] / 2) * PX, (age["y"] + 1.5) * PX
    cv2.line(img, (round(x), round(y)), (round(x), round(y + 4.5 * PX)), 0, 4)
    return img


@pytest.mark.parametrize("case", CASES)
def test_register_degraded_scans(case, rendered, filled_page):
    scan, matrix = degrade(filled_page, CASES[case])
    reg = register(scan, lambda h: rendered.layout if h == rendered.layout_hash else None)
    assert (reg.ref.sheet_id, reg.ref.page, reg.ref.pages) == (SHEET, 1, 2)
    assert reg.markers == [0, 1, 2, 3]
    assert reg.residual_mm < 0.5

    # True error at every answer area: page mm → original px → scan px → registered mm.
    marks = [m for m in rendered.layout["marks"] if m["page"] == 1]
    truth = np.array([[m["x"] + m["w"] / 2, m["y"] + m["h"] / 2] for m in marks])
    scan_px = cv2.perspectiveTransform((truth * PX).reshape(-1, 1, 2), matrix)
    back = cv2.perspectiveTransform(scan_px, reg.homography).reshape(-1, 2)
    assert np.linalg.norm(back - truth, axis=1).max() < 0.5

    fields = crops(reg)
    consent = {c.mark["v"]: ink(c.image) for c in fields["consent"]}
    assert consent["yes"] > 0.8 and consent["no"] < 0.02
    assert ink(fields["age"][0].image) > 0.03
    assert ink(fields["age"][1].image) < 0.01
    blank = [
        ink(c.image)
        for fid, items in fields.items()
        if fid not in ("consent", "age")
        for c in items
        if c.mark["k"] != "area"
    ]
    assert max(blank) < 0.01


def test_too_few_markers(rendered, filled_page):
    img = filled_page.copy()
    for m in rendered.layout["markers"][:2]:
        x0, y0 = round((m["x"] - 1) * PX), round((m["y"] - 1) * PX)
        img[y0 : y0 + round((m["h"] + 2) * PX), x0 : x0 + round((m["w"] + 2) * PX)] = 255
    with pytest.raises(KffError, match="Only 2 of the 4 corner markers"):
        register(img, lambda h: rendered.layout)


def test_unknown_layout(filled_page):
    with pytest.raises(KffError, match="isn't in this store"):
        register(filled_page, lambda h: None)


def test_not_a_kff_page():
    blank = np.full((2000, 1400), 255, np.uint8)
    with pytest.raises(KffError):
        register(blank, lambda h: None)


@pytest.mark.parametrize(
    "text", ["kff1|a|b", "KFF1:XYZ:00:1/2", f"KFF1:1A2B3C4D:{SHEET.upper()}:3/2", "hello"]
)
def test_parse_payload_rejects(text):
    with pytest.raises(KffError):
        parse_payload(text)


def test_parse_payload_roundtrip():
    ref = parse_payload(qr_payload("1a2b3c4d", SHEET, 2, 3))
    assert (ref.layout_hash, ref.sheet_id, ref.page, ref.pages) == ("1a2b3c4d", SHEET, 2, 3)


def test_scan_the_pdf_itself_into_the_store(tmp_path, form, rendered):
    """The rendered PDF, rasterised, is a perfect scan: both pages register, the sheet is
    complete, and a rescan replaces the earlier scan."""
    with Store(tmp_path) as store:
        store.save_form(form)
        store.save_layout(rendered.layout)
        store.save_sheets(rendered.layout_hash, "b1", [SHEET])
        results = scan_file(store, Path("sheets.pdf"), data=rendered.pdf)
        assert [r.error for r in results] == [None, None]
        assert all(r.notes == [] for r in results)
        [status] = sheet_status(store, [SHEET])
        assert (status.found, status.missing, status.code) == ([1, 2], [], "0123-4567")
        again = scan_file(store, Path("again.pdf"), data=rendered.pdf)
        assert "replaces an earlier scan of this page" in again[0].notes
        assert len(store.scans(SHEET)) == 2
        assert (tmp_path / store.scans(SHEET)[0]["image"]).exists()


def test_scan_reports_unprinted_sheets(tmp_path, form, rendered):
    with Store(tmp_path) as store:
        store.save_form(form)
        store.save_layout(rendered.layout)
        [first, _] = scan_file(store, Path("x.pdf"), data=rendered.pdf)
    assert any("photocopy" in n for n in first.notes)


def test_load_rejects_junk(tmp_path):
    with pytest.raises(KffError, match="not a PDF or an image"):
        list(load(tmp_path / "notes.txt", data=b"hello"))


def test_cli_scan(tmp_path, monkeypatch, capsys, form, filled_page, rendered):
    monkeypatch.setenv("KFF_HOME", str(tmp_path))
    with Store(tmp_path) as store:
        store.save_form(form)
        store.save_layout(rendered.layout)
        store.save_sheets(rendered.layout_hash, "b1", [SHEET])
    scan, _ = degrade(filled_page, CASES["phone photo"])
    photo = tmp_path / "photo.jpg"
    cv2.imwrite(str(photo), scan)
    junk = tmp_path / "junk.png"
    cv2.imwrite(str(junk), np.full((500, 400), 255, np.uint8))

    assert main(["scan", str(photo), str(junk), "--debug", str(tmp_path / "dbg")]) == 1
    out = capsys.readouterr()
    assert "photo.jpg: sheet 0123-4567 page 1/2  markers 4/4" in out.out
    assert "junk.png: FAILED" in out.out
    assert "sheet 0123-4567: 1/2 pages  missing pages [2]" in out.out
    assert "1 page(s) couldn't be read" in out.err
    assert (tmp_path / "dbg" / "0123-4567-p1-overlay.png").exists()
    assert (tmp_path / "dbg" / "0123-4567-p1-consent-0.png").exists()
