"""Measure the read side on synthetic sheets: render, fill in, degrade like a scan, scan,
read, and compare every field with the answers that were written."""

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from kff.form.rules import rules
from kff.read.sheet import read_sheet
from kff.read.vlm import VlmReader
from kff.render.options import RenderOptions
from kff.render.pdf import Rendered, render
from kff.scan.load import PageImage
from kff.scan.pipeline import scan_page
from kff.store import FormVersion, Store, new_batch_id
from kff.synth.answers import SheetAnswers, random_answers
from kff.synth.degrade import Degrade, degrade
from kff.synth.fill import filled_pages


def random_scan(rng: random.Random) -> Degrade:
    """A plausible scan or phone photo."""
    phone = rng.random() < 0.3
    return Degrade(
        rotate_deg=rng.uniform(-3, 3) + (rng.uniform(-2, 2) if phone else 0),
        scale=rng.uniform(0.75, 1.0),
        perspective=rng.uniform(0.01, 0.04) if phone else 0.0,
        upside_down=rng.random() < 0.1,
        blur=rng.uniform(0.0, 1.0),
        noise=rng.uniform(0, 6),
        jpeg_quality=rng.randint(55, 90),
        pad_px=rng.randint(20, 120),
        seed=rng.randint(0, 1 << 30),
    )


def same(qtype: str, truth: str | None, read: str | None) -> bool:
    if truth is None or read is None:
        return truth is None and read is None
    if qtype in ("integer", "range"):
        return int(truth) == int(read)
    if qtype == "decimal":
        return math.isclose(float(truth), float(read), abs_tol=1e-9)
    if qtype == "select_multiple":
        return set(truth.split()) == set(read.split())
    if qtype == "geopoint":
        a, b = truth.split()[:2], read.split()[:2]
        return all(
            math.isclose(float(x), float(y), abs_tol=1e-5) for x, y in zip(a, b, strict=True)
        )
    if qtype in ("text", "barcode"):
        return " ".join(truth.split()).casefold() == " ".join(read.split()).casefold()
    if qtype == "image":
        return True
    return truth == read


@dataclass
class Outcome:
    field_id: str
    type: str
    truth: str | None
    read: str | None
    ok: bool
    flagged: bool
    reason: str | None


@dataclass
class BenchResult:
    profile: str
    sheets: int
    pages: int
    scan_failures: list[str] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Per question type: raw accuracy; how many readings were flagged for review; and
        wrong readings that were *not* flagged, which would reach Kobo without a person
        checking them (the number that matters most)."""
        by_type: dict[str, Counter] = defaultdict(Counter)
        for o in self.outcomes:
            c = by_type[o.type]
            c["fields"] += 1
            c["correct"] += o.ok
            c["flagged"] += o.flagged
            c["wrong_unflagged"] += (not o.ok) and (not o.flagged)
        total = sum((c for c in by_type.values()), Counter())
        return {
            "profile": self.profile,
            "sheets": self.sheets,
            "pages": self.pages,
            "scan_failures": self.scan_failures,
            "by_type": {t: _rates(c) for t, c in sorted(by_type.items())},
            "all": _rates(total),
            # The first wrong readings, to see what goes wrong, not just how often.
            "wrong": [
                {
                    "field": o.field_id,
                    "type": o.type,
                    "written": o.truth,
                    "read": o.read,
                    "flagged": o.flagged,
                    "reason": o.reason,
                }
                for o in self.outcomes
                if not o.ok
            ][:40],
        }


def _rates(c: Counter) -> dict[str, Any]:
    n = max(1, c["fields"])
    return {
        **c,
        "accuracy": round(c["correct"] / n, 3),
        "flagged_rate": round(c["flagged"] / n, 3),
        "wrong_unflagged_rate": round(c["wrong_unflagged"] / n, 3),
    }


def compare(
    rendered: Rendered, answers: SheetAnswers, readings: dict[str, dict[str, Any]]
) -> list[Outcome]:
    truth = answers.values()
    out = []
    for fid, info in rendered.layout["fields"].items():
        r = readings.get(fid)
        if r is None:
            continue
        t, v = truth.get(fid), r["value"]
        if info["type"] == "image":
            t = "signature" if t else None
            v = "signature" if v else None
        out.append(
            Outcome(
                fid,
                info["type"],
                t,
                v,
                same(info["type"], t, v),
                bool(r["needs_review"]),
                r["reason"],
            )
        )
    return out


def run(
    store: Store,
    form: FormVersion,
    options: RenderOptions,
    *,
    sheets: int = 3,
    seed: int = 0,
    vlm: VlmReader | None = None,
    profile: str = "",
    ppi: int = 200,
) -> tuple[BenchResult, list[tuple[str, SheetAnswers]]]:
    """Synthetic sheets through scan and read. Returns the result and, per sheet, the
    answers written (for a submission check)."""
    rng = random.Random(seed)
    form = store.save_form(form)
    rendered = render(form, options, copies=sheets)
    store.save_layout(rendered.layout)
    store.save_sheets(rendered.layout_hash, new_batch_id(), [s.id for s in rendered.sheets])
    form_rules = rules(form.content)
    result = BenchResult(profile or options.paper, sheets, sheets * rendered.pages_per_sheet)
    written: list[tuple[str, SheetAnswers]] = []
    for i, sheet in enumerate(rendered.sheets):
        answers = random_answers(rendered.model, form_rules, rng)
        for p, png in enumerate(filled_pages(rendered, answers, rng, sheet_index=i, ppi=ppi), 1):
            img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
            scan, _ = degrade(img, random_scan(rng))
            page = scan_page(store, PageImage(f"synthetic-{sheet.code}-p{p}", scan))
            if page.error:
                result.scan_failures.append(f"{sheet.code} p{p}: {page.error}")
        read_sheet(store, sheet.id, vlm)
        result.outcomes += compare(rendered, answers, store.readings(sheet.id))
        written.append((sheet.id, answers))
    return result, written
