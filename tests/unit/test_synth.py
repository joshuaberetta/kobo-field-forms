import json
import random
from pathlib import Path

import cv2
import numpy as np
import pytest

from kff.form.rules import rules
from kff.render.options import RenderOptions
from kff.render.pdf import render
from kff.scan.register import register
from kff.store import FormVersion
from kff.synth.answers import EASTERN_DIGITS, random_answers
from kff.synth.fill import fill_items, filled_pages, random_hand

CONTENT = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "forms" / "all_types.json").read_text()
)
RULES = rules(CONTENT)


@pytest.fixture(scope="module")
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


@pytest.fixture(scope="module")
def rendered(form):
    return render(form)


@pytest.mark.parametrize("seed", range(30))
def test_answers_follow_skip_logic_and_constraints(rendered, seed):
    values = random_answers(rendered.model, RULES, random.Random(seed)).values()
    assert ("hh/district_other" in values) <= (values.get("hh/district") == "other")
    assert ("hh/phones" in values) <= ("phone" in values.get("hh/assets", "").split())
    if values.get("consent") != "yes":
        assert not [k for k in values if k.startswith("hh/")]
    assert 0 <= int(values["age"]) <= 120  # required, `. >= 0 and . <= 120`
    if "hh/assets" in values:
        order = ["phone", "radio", "tv", "bicycle", "motorbike", "fridge"]
        picked = values["hh/assets"].split()
        assert picked == sorted(picked, key=order.index)


def test_arabic_sheets_sometimes_use_eastern_digits(form):
    r = render(form, RenderOptions(languages=("ar",)))
    written = "".join(
        ch
        for seed in range(10)
        for a in random_answers(r.model, RULES, random.Random(seed)).answers.values()
        for ch in a.cells.values()
    )
    assert set(written) & set(EASTERN_DIGITS) and set(written) & set("0123456789")
    texts = [
        a.text for a in random_answers(r.model, RULES, random.Random(1)).answers.values() if a.text
    ]
    assert any("؀" <= t[0] <= "ۿ" for t in texts)  # Arabic text answers on lines


def test_every_written_cell_and_ticked_choice_is_drawn(rendered):
    rng = random.Random(3)
    answers = random_answers(rendered.model, RULES, rng)
    items = fill_items(rendered.layout, answers, random_hand(rng), rng)
    chars = sum(len(a.cells) for a in answers.answers.values())
    ticks = sum(len(a.choices) for a in answers.answers.values())
    assert sum(1 for i in items if i["k"] == "char") == chars
    assert sum(1 for i in items if i["k"] in ("blob", "cross")) == ticks


def test_filled_sheet_still_registers(rendered):
    rng = random.Random(4)
    answers = random_answers(rendered.model, RULES, rng)
    png = filled_pages(rendered, answers, rng, ppi=150)[0]
    reg = register(cv2.imdecode(np.frombuffer(png, np.uint8), 0), lambda h: rendered.layout)
    assert reg.ref.page == 1 and reg.residual_mm < 0.5
