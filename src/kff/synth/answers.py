"""Random but valid answers for a printable model: what a person would write on a sheet.

Each answer has the value Kobo should end up with (`value`, in submission format) and how
it's written on paper (`paper`: choices ticked, characters per comb cell, a line of text).
Skip logic is followed: questions that wouldn't be shown are left blank.
"""

import random
from dataclasses import dataclass, field
from typing import Any

from kff.form.model import PrintModel, Question, _max_digits
from kff.form.rules import Rules

LATIN_FIRST = ["AMINA", "JOSEPH", "FATUMA", "PETER", "GRACE", "DAVID", "MARIA", "JOHN", "ROSE"]
LATIN_LAST = ["ODHIAMBO", "KAMAU", "MWANGI", "OTIENO", "SILVA", "DIALLO", "KONE", "MENSAH"]
ARABIC_FIRST = ["أحمد", "فاطمة", "محمد", "مريم", "علي", "سارة", "يوسف", "ليلى", "عمر", "نور"]
ARABIC_LAST = ["حسن", "خليل", "سعيد", "منصور", "عباس", "حداد", "صالح"]
LATIN_PHRASES = ["No problems", "Needs follow up", "Water point is far", "Moved last year"]
ARABIC_PHRASES = ["لا توجد مشاكل", "يحتاج إلى متابعة", "مصدر الماء بعيد", "انتقلوا العام الماضي"]
EASTERN_DIGITS = "٠١٢٣٤٥٦٧٨٩"


@dataclass
class Answer:
    value: str | None  # as Kobo stores it; None: left blank
    choices: list[str] = field(default_factory=list)
    cells: dict[str, str] = field(default_factory=dict)  # "segment:index" → character
    text: str | None = None  # lines and boxes
    script: str = "latin"


@dataclass
class SheetAnswers:
    answers: dict[str, Answer]  # by field id

    def values(self) -> dict[str, str]:
        return {k: a.value for k, a in self.answers.items() if a.value is not None}


def _write(segments: list[str], q: Question, digits: str | None, right: bool) -> dict[str, str]:
    """Characters into comb cells, segment by segment (left- or right-aligned)."""
    cells: dict[str, str] = {}
    segs = [s for row in q.widget.rows for s in row.segments]
    seg_ids = [
        ri * 100 + si for ri, row in enumerate(q.widget.rows) for si in range(len(row.segments))
    ]
    for text, seg, sid in zip(segments, segs, seg_ids, strict=True):
        text = text[: seg.cells]
        start = seg.cells - len(text) if right and seg.charset == "digit" else 0
        for i, ch in enumerate(text):
            if ch == " ":
                continue
            if digits and ch.isdigit():
                ch = digits[int(ch)]
            cells[f"{sid}:{start + i}"] = ch
    return cells


def _digits(rng: random.Random, n: int, lo: int = 0, hi: int | None = None) -> int:
    hi = 10**n - 1 if hi is None else hi
    return rng.randint(lo, max(lo, hi))


def answer_for(q: Question, rng: random.Random, script: str, rules: Rules) -> Answer:
    w = q.widget
    eastern = EASTERN_DIGITS if script == "arabic" and rng.random() < 0.5 else None
    right = rng.random() < 0.3
    if w.kind == "choices":
        values = [c.value for c in w.choices]
        if q.type == "acknowledge":
            return Answer("OK", ["OK"])
        if w.multiple:
            picked = rng.sample(values, k=min(len(values), rng.randint(1, 3)))
            picked = [v for v in values if v in picked]  # form order, as Kobo stores
            return Answer(" ".join(picked), picked)
        v = rng.choice(values)
        return Answer(v, [v])
    if w.kind == "lines":
        if script == "arabic":
            text = rng.choice(ARABIC_PHRASES if w.lines > 1 else ARABIC_FIRST)
            if w.lines == 1:
                text = f"{text} {rng.choice(ARABIC_LAST)}"
        else:
            text = rng.choice(LATIN_PHRASES)
        return Answer(text, text=text, script=script)
    if w.kind == "box":
        name = rng.choice(ARABIC_FIRST if script == "arabic" else LATIN_FIRST).title()
        return Answer("signature", text=name, script=script)
    if w.kind != "comb":
        return Answer(None)

    segs = [s for row in w.rows for s in row.segments]
    t = q.type
    if t in ("integer", "range"):
        for _ in range(20):
            n = _digits(rng, segs[0].cells, 0, min(10 ** segs[0].cells - 1, _hi(q, rules)))
            if rules.constraint_ok(q.id, str(n), {}) is not False:
                break
        return Answer(str(n), cells=_write([str(n)], q, eastern, right))
    if t == "decimal":
        whole = _digits(rng, segs[0].cells, 0, 10 ** min(segs[0].cells, 3) - 1)
        frac = rng.randint(0, 99)
        return Answer(
            f"{whole}.{frac:02d}", cells=_write([str(whole), f"{frac:02d}"], q, eastern, right)
        )
    if t in ("date", "datetime"):
        y, m, d = rng.randint(2020, 2026), rng.randint(1, 12), rng.randint(1, 28)
        parts = [f"{d:02d}", f"{m:02d}", str(y)]
        if t == "date":
            return Answer(f"{y}-{m:02d}-{d:02d}", cells=_write(parts, q, eastern, False))
        hh, mm = rng.randint(0, 23), rng.randint(0, 59)
        value = f"{y}-{m:02d}-{d:02d}T{hh:02d}:{mm:02d}:00.000+00:00"
        return Answer(value, cells=_write([*parts, f"{hh:02d}", f"{mm:02d}"], q, eastern, False))
    if t == "time":
        hh, mm = rng.randint(0, 23), rng.randint(0, 59)
        return Answer(
            f"{hh:02d}:{mm:02d}:00.000+00:00",
            cells=_write([f"{hh:02d}", f"{mm:02d}"], q, eastern, False),
        )
    if t == "geopoint":
        lat, lon = rng.uniform(-40, 40), rng.uniform(-170, 170)
        written = []
        for v, whole_cells in ((lat, segs[1].cells), (lon, segs[4].cells)):
            whole, frac = f"{abs(v):.5f}".split(".")
            written += ["-" if v < 0 else "+", whole.zfill(whole_cells), frac]
        value = f"{round(lat, 5):.5f} {round(lon, 5):.5f} 0 0"
        return Answer(value, cells=_write(written, q, eastern, False))
    # Text in letter boxes (capitals), or digits-only text.
    cells = segs[0].cells
    if segs[0].charset == "digit":
        text = "".join(rng.choice("0123456789") for _ in range(rng.randint(6, min(cells, 12))))
    else:
        text = f"{rng.choice(LATIN_FIRST)} {rng.choice(LATIN_LAST)}"[:cells].strip()
    return Answer(text, cells=_write([text], q, None, False))


def _hi(q: Question, rules: Rules) -> int:
    row = rules.rows.get(q.xpath, {})
    return 10 ** _max_digits(row.get("constraint"), 4) - 1 if row.get("constraint") else 10**4


def random_answers(
    model: PrintModel, rules: Rules, rng: random.Random, blank_rate: float = 0.1
) -> SheetAnswers:
    script = model.languages[0].script
    answers: dict[str, Answer] = {}
    for q in model.questions():
        if q.widget.kind == "none":
            continue
        if not q.required and rng.random() < blank_rate:
            continue
        a = answer_for(q, rng, script, rules)
        if a.value is not None:
            answers[q.id] = a
    # Leave out what skip logic would hide; repeat until nothing changes, since hiding
    # one answer can hide another.
    while True:
        values = {k: a.value for k, a in answers.items()}
        hidden = [k for k in answers if not rules.relevant(k, values)]
        if not hidden:
            break
        for k in hidden:
            del answers[k]
    return SheetAnswers(answers)


def as_json(answers: SheetAnswers) -> dict[str, Any]:
    return {k: a.value for k, a in answers.answers.items()}
