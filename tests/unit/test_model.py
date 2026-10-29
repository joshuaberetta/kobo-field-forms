import json
from pathlib import Path

import pytest

from kff.form.model import (
    ModelOptions,
    Section,
    _max_digits,
    _Refs,
    build_model,
    describe_condition,
    pick_language,
)

CONTENT = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "forms" / "all_types.json").read_text()
)


@pytest.fixture(scope="module")
def model():
    return build_model(CONTENT, "All types")


def by_id(model):
    return {q.id: q for q in model.questions()}


def test_questions_numbered_in_order_and_silent_rows_dropped(model):
    qs = model.questions()
    ids = [q.id for q in qs]
    assert ids[:4] == ["full_name", "age", "consent", "hh/district"]
    # calculate, start and end aren't printed
    assert not {"is_adult", "start", "end"} & {q.name for q in qs}
    numbers = [q.number for q in qs if "[2]" not in q.id]
    assert numbers == list(range(1, len(numbers) + 1))


def test_repeat_instances_have_their_own_ids_and_share_numbers(model):
    qs = by_id(model)
    assert qs["hh/members[1]/member_age"].number == qs["hh/members[2]/member_age"].number
    assert qs["hh/members[2]/member_age"].xpath == "hh/members/member_age"
    hh = next(i for i in model.items if isinstance(i, Section) and i.id == "hh")
    repeats = [i for i in hh.children if isinstance(i, Section)]
    assert [(r.id, r.instance, r.instances) for r in repeats] == [
        ("hh/members[1]", 1, 2),
        ("hh/members[2]", 2, 2),
    ]


def test_open_repeat_uses_repeat_rows():
    content = {
        **CONTENT,
        "survey": [
            {"type": "begin_repeat", "name": "r", "label": ["R", "R"]},
            {"type": "text", "name": "t", "label": ["T", "T"]},
            {"type": "end_repeat"},
        ],
    }
    m = build_model(content, "x", ModelOptions(repeat_rows=4))
    assert [q.id for q in m.questions()] == [f"r[{i}]/t" for i in range(1, 5)]


def test_widgets(model):
    qs = by_id(model)
    cells = {k: [[s.cells for s in r.segments] for r in q.widget.rows] for k, q in qs.items()}
    assert cells["age"] == [[3]]  # . <= 120
    assert cells["hh/income"] == [[4, 2]]  # . < 10000
    assert cells["hh/visit_date"] == [[2, 2, 4]]
    assert cells["hh/visit_time"] == [[2, 2]]
    assert cells["hh/location"] == [[1, 2, 5], [1, 3, 5]]
    assert cells["rating"] == [[2]]  # end=10
    assert qs["full_name"].widget.rows[0].segments[0].charset == "text"
    assert qs["comments"].widget.kind == "lines"
    assert qs["signature"].widget.kind == "box"
    assert qs["voice_note"].widget.kind == "none"
    assert qs["hh/assets"].widget.multiple and not qs["consent"].widget.multiple
    assert [c.value for c in qs["confirm"].widget.choices] == ["OK"]


def test_choice_layouts(model):
    qs = by_id(model)
    assert qs["consent"].widget.layout == "horizontal"
    assert (qs["hh/district"].widget.layout, qs["hh/district"].widget.columns) == ("columns", 3)


def test_conditions_in_words(model):
    qs = by_id(model)
    assert qs["hh/district_other"].condition == "Q4 is “Other”"
    assert qs["hh/phones"].condition == "Q7 includes “Mobile phone”"
    hh = next(i for i in model.items if isinstance(i, Section) and i.id == "hh")
    assert hh.condition == "Q3 is “Yes”"


@pytest.mark.parametrize(
    ("expr", "words"),
    [
        ("${a} > 3 and ${b} != 'x'", "Q1 is more than 3 and Q2 is not “X”"),
        ("not(selected(${b}, 'x'))", "Q2 doesn't include “X”"),
        ("${a} = ''", "Q1 is blank"),
        ("${a} != ''", "Q1 is answered"),
        ("${unknown} >= 18", "“unknown” is at least 18"),
        ("count-selected(${b}) > 1", "count-selected(Q2) > 1"),
        ("", None),
    ],
)
def test_describe_condition(expr, words):
    refs = _Refs()
    refs.numbers = {"a": 1, "b": 2}
    refs.choice_labels = {"b": {"x": "X"}}
    assert describe_condition(expr, refs) == words


def test_labels_in_the_chosen_language():
    m = build_model(CONTENT, "x", ModelOptions(language="ar"))
    assert m.language == "Arabic (ar)"
    assert by_id(m)["age"].label == "العمر بالسنوات"
    assert by_id(m)["consent"].widget.choices[0].label == "نعم"


@pytest.mark.parametrize(("wanted", "index"), [(None, 0), ("ar", 1), ("Arabic", 1), ("en", 0)])
def test_pick_language(wanted, index):
    assert pick_language(CONTENT, wanted) == index


def test_unknown_language():
    with pytest.raises(ValueError, match="no language 'fr'"):
        pick_language(CONTENT, "fr")


def test_unsupported_types_warn(model):
    assert model.warnings == ["voice_note: audio recording can't be answered on paper; left out."]


@pytest.mark.parametrize(
    ("constraint", "digits"),
    [(". <= 120", 3), (". < 100", 2), ("100 > .", 2), (". >= 0 and . <= 9", 1), (None, 4)],
)
def test_max_digits(constraint, digits):
    assert _max_digits(constraint, 4) == digits
