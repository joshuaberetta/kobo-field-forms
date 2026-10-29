import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from kff.render.pdf import render
from kff.store import FormVersion
from kff.submit import assemble, build_xml, submission_values

CONTENT = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "forms" / "all_types.json").read_text()
)
SHEET = "0123456789abcdef0123456789abcdef"


@pytest.fixture(scope="module")
def form():
    return FormVersion(
        "kf.kobotoolbox.org",
        "aAllTypes000000000000",
        "vAllTypes000000000000",
        "All question types",
        "3",
        "2026-10-01T10:00:00.123Z",
        "f" * 32,
        "h",
        CONTENT,
    )


@pytest.fixture(scope="module")
def layout(form):
    return render(form).layout


def reading(value, needs_review=False, reviewed=False, reason=None):
    return {"value": value, "needs_review": needs_review, "reviewed": reviewed, "reason": reason}


def base_readings():
    return {
        "full_name": reading("ANN"),
        "age": reading("34"),
        "consent": reading("yes"),
        "hh/district": reading("d1"),
        "hh/district_other": reading("Somewhere"),  # hidden: district isn't "other"
        "hh/members[1]/member_name": reading("BOB"),
        "hh/members[1]/member_age": reading("5"),
    }


def test_assemble_drops_hidden_answers(form, layout):
    a = assemble(form, layout, base_readings())
    assert a.ok, a.problems
    assert "hh/district_other" not in a.answers and a.dropped == ["hh/district_other"]


def test_assemble_flags_problems(form, layout):
    r = base_readings()
    r["age"] = reading("130")  # breaks `. <= 120`
    del r["full_name"]  # required
    r["hh/district"] = reading("d2", needs_review=True, reason="faint mark")
    a = assemble(form, layout, r)
    problems = dict(a.problems)
    assert "constraint" in problems["age"]
    assert "required" in problems["full_name"]
    assert problems["hh/district"] == "faint mark"
    # An empty repeat entry isn't a missing answer.
    assert not [p for p in problems if p.startswith("hh/members[2]")]
    assert assemble(form, layout, {**r, "hh/district": reading("d2", True, True)}).problems != []
    accepted = assemble(form, layout, r, accept_flagged=True)
    assert "hh/district" not in dict(accepted.problems)


def test_whole_section_hidden(form, layout):
    r = base_readings()
    r["consent"] = reading("no")
    a = assemble(form, layout, r)
    assert not [k for k in a.answers if k.startswith("hh/")]


def test_build_xml(form):
    answers = {
        "full_name": "ANN",
        "consent": "yes",
        "hh/district": "d1",
        "hh/members[1]/member_name": "BOB",
        "hh/members[3]/member_name": "CY",
    }
    root = ET.fromstring(build_xml(form, answers, SHEET))
    assert root.tag == form.asset_uid
    assert root.attrib == {"id": form.asset_uid, "version": "3 (2026-10-01 10:00:00)"}
    assert root.findtext("formhub/uuid") == "f" * 32
    assert root.findtext("full_name") == "ANN"
    members = root.findall("hh/members")
    assert [m.findtext("member_name") for m in members] == ["BOB", "CY"]
    assert root.findtext("__version__") == form.version_uid
    assert root.findtext("meta/instanceID") == "uuid:01234567-89ab-cdef-0123-456789abcdef"
    assert root.find("comments") is None  # unanswered: no element


def test_submission_values_round_trip():
    record = {
        "_id": 1,
        "full_name": "ANN",
        "hh/district": "d1",
        "hh/members": [{"hh/members/member_name": "BOB"}, {"hh/members/member_name": "CY"}],
        "meta/instanceID": "uuid:x",
        "formhub/uuid": "u",
    }
    assert submission_values(record) == {
        "full_name": "ANN",
        "hh/district": "d1",
        "hh/members[1]/member_name": "BOB",
        "hh/members[2]/member_name": "CY",
    }
