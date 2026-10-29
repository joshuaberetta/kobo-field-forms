"""Read synthetic filled-in sheets back: render, fill, degrade, scan, read, compare."""

import json
from pathlib import Path

import pytest

from kff.bench import run
from kff.read.digits import normalize, read_digits
from kff.read.sheet import read_sheet
from kff.read.vlm import TextReading
from kff.render.options import RenderOptions
from kff.store import FormVersion, Store

CONTENT = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "forms" / "all_types.json").read_text()
)


class FakeVlm:
    def __init__(self):
        self.prompts = []

    def read(self, image, prompt):
        self.prompts.append(prompt)
        return TextReading("TEXT", 0.9)


@pytest.fixture
def form():
    return FormVersion(
        "kf.kobotoolbox.org",
        "aAllTypes000000000000",
        "vAllTypes000000000000",
        "All question types",
        "3",
        "2026-10-01T00:00:00Z",
        "u",
        "h",
        CONTENT,
    )


def by_type(result):
    return result.summary()["by_type"]


def test_bubbles_and_digits_read_from_synthetic_scans(tmp_path, form):
    with Store(tmp_path) as store:
        result, _ = run(store, form, RenderOptions(), sheets=3, seed=11)
    assert not result.scan_failures
    stats = by_type(result)
    for qtype in ("select_one", "select_multiple", "acknowledge"):
        assert stats[qtype]["accuracy"] == 1.0, (qtype, stats[qtype])
    for qtype in ("integer", "date", "time"):
        assert stats[qtype]["accuracy"] >= 0.6, (qtype, stats[qtype])
    # Without a text reader, written text is flagged, never guessed.
    assert stats["text"]["wrong_unflagged"] <= stats["text"]["fields"] - stats["text"]["flagged"]
    assert all(o.flagged for o in result.outcomes if o.type == "text" and o.truth and not o.read)


def test_text_goes_to_the_vision_model(tmp_path, form):
    vlm = FakeVlm()
    with Store(tmp_path) as store:
        result, written = run(store, form, RenderOptions(), sheets=1, seed=3, vlm=vlm)
        readings = store.readings(written[0][0])
    texts = [r for fid, r in readings.items() if r["method"] == "vlm"]
    assert texts and all(r["value"] == "TEXT" for r in texts)
    assert any("Question: Full name" in p for p in vlm.prompts)
    assert any("one per box" in p for p in vlm.prompts)


def test_reviewed_readings_survive_a_reread(tmp_path, form):
    with Store(tmp_path) as store:
        _, written = run(store, form, RenderOptions(), sheets=1, seed=5)
        sheet_id = written[0][0]
        store.review(sheet_id, "age", "41")
        read_sheet(store, sheet_id)
        r = store.readings(sheet_id)["age"]
    assert (r["value"], r["reviewed"], r["method"]) == ("41", 1, "person")


def test_blank_cells_read_as_blank():
    import numpy as np

    blank = np.full((49, 37), 240, np.uint8)
    assert normalize(blank) is None
    [r] = read_digits([blank], ["0123456789"])
    assert r.value is None


@pytest.mark.parametrize("seed", [1, 2])
def test_arabic_sheet(tmp_path, form, seed):
    with Store(tmp_path) as store:
        result, _ = run(store, form, RenderOptions(languages=("ar", "en")), sheets=2, seed=seed)
    assert not result.scan_failures
    assert by_type(result)["select_one"]["accuracy"] == 1.0


@pytest.mark.parametrize(
    ("blank", "pattern"),
    [
        ([False] * 4 + [True] + [False] * 6 + [True] * 3, "#### ######"),
        ([True, False, False, True, True], "##"),
        ([True] * 5, ""),
    ],
)
def test_box_pattern(blank, pattern):
    from kff.read.sheet import box_pattern

    assert box_pattern(blank) == pattern


@pytest.mark.parametrize(
    ("read", "pattern", "text", "flagged"),
    [
        ("DAVID MENS AH", "##### ######", "DAVID MENSAH", False),  # space from a box gap
        ("DAVIDMENSAH", "##### ######", "DAVID MENSAH", False),
        ("AMINA OTENO", "##### ######", "AMINA OTENO", True),  # a letter dropped
        ("JOHN", "####", "JOHN", False),
    ],
)
def test_fit_to_boxes(read, pattern, text, flagged):
    from kff.read.sheet import fit_to_boxes

    got, reason = fit_to_boxes(read, pattern)
    assert got == text and (reason is not None) == flagged


class ScriptedVlm:
    """Answers with fixed text and confidence."""

    def __init__(self, text, confidence=0.95):
        self.text, self.confidence, self.prompts = text, confidence, []

    def read(self, image, prompt):
        self.prompts.append(prompt)
        return TextReading(self.text, self.confidence)


def test_reader_text_is_checked_against_the_boxes(tmp_path, form):
    """A confident reading with a dropped letter is flagged; text on lines always is."""
    vlm = ScriptedVlm("X")
    with Store(tmp_path) as store:
        _, written = run(store, form, RenderOptions(), sheets=1, seed=3, vlm=vlm)
        readings = store.readings(written[0][0])
    boxes = [r for r in readings.values() if r["method"] == "vlm" and "boxes" in r["detail"]]
    lines = [r for r in readings.values() if r["method"] == "vlm" and "boxes" not in r["detail"]]
    assert boxes and all(r["needs_review"] and "boxes are written in" in r["reason"] for r in boxes)
    assert all(r["needs_review"] and "on lines" in r["reason"] for r in lines)
    assert any("letters)" in p and "`" in p for p in vlm.prompts)  # the box pattern is sent
