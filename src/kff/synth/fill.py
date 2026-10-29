"""Draw answers onto a rendered sheet as a person might: handwriting fonts with jitter in
the boxes, bubbles filled unevenly (or crossed), text on the lines. The result is a page
raster for `synth.degrade` to turn into a "scan"."""

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kff.render.pdf import Rendered, render_png
from kff.synth.answers import SheetAnswers

FONTS = Path(__file__).with_name("fonts")
LATIN_HANDS = ["Patrick Hand", "Indie Flower", "Kalam", "Gochi Hand"]
ARABIC_HANDS = ["Aref Ruqaa"]
PENS = ["#1b2a6b", "#111111", "#20305f", "#2b2b2b"]


@dataclass(frozen=True)
class Hand:
    """One person's handwriting for a whole sheet."""

    latin: str
    arabic: str
    pen: str
    size: float  # character height as a share of the cell height
    wobble: float  # mm of random offset
    tilt: float  # degrees of random rotation
    cross_rate: float  # share of ticked boxes marked with an X instead of filled


def random_hand(rng: random.Random) -> Hand:
    return Hand(
        latin=rng.choice(LATIN_HANDS),
        arabic=rng.choice(ARABIC_HANDS),
        pen=rng.choice(PENS),
        size=rng.uniform(0.7, 0.9),
        wobble=rng.uniform(0.2, 0.6),
        tilt=rng.uniform(2, 8),
        cross_rate=rng.choice([0.0, 0.0, 0.3]),
    )


def fill_items(
    layout: dict[str, Any], answers: SheetAnswers, hand: Hand, rng: random.Random
) -> list[dict[str, Any]]:
    """Drawing instructions for the template's fill layer, in page mm."""
    items: list[dict[str, Any]] = []
    jitter = lambda s=1.0: rng.uniform(-hand.wobble, hand.wobble) * s  # noqa: E731
    for m in layout["marks"]:
        a = answers.answers.get(m["f"])
        if a is None:
            continue
        base = {
            "page": m["page"],
            "x": m["x"],
            "y": m["y"],
            "w": m["w"],
            "h": m["h"],
            "pen": hand.pen,
        }
        if m["k"] == "cell":
            ch = a.cells.get(f"{m['s']}:{m['i']}")
            if not ch:
                continue
            arabic = "؀" <= ch <= "ۿ"
            items.append(
                {
                    **base,
                    "k": "char",
                    "text": ch,
                    "font": hand.arabic if arabic else hand.latin,
                    "size": m["h"] * hand.size * rng.uniform(0.9, 1.05) * 2.83,
                    "dx": jitter(),
                    "dy": jitter(0.6),
                    "rot": rng.uniform(-1, 1) * hand.tilt,
                }
            )
        elif m["k"] == "choice" and m["v"] in a.choices:
            if rng.random() < hand.cross_rate:
                items.append({**base, "k": "cross", "dx": jitter(0.5), "dy": jitter(0.5)})
            else:
                items.append(
                    {
                        **base,
                        "k": "blob",
                        "dx": jitter(0.4),
                        "dy": jitter(0.4),
                        "rx": m["w"] / 2 * rng.uniform(0.7, 0.95),
                        "ry": m["h"] / 2 * rng.uniform(0.65, 0.95),
                    }
                )
        elif m["k"] == "area" and a.text:
            arabic = a.script == "arabic"
            items.append(
                {
                    **base,
                    "k": "text",
                    "text": a.text,
                    "font": hand.arabic if arabic else hand.latin,
                    "dir": "rtl" if arabic else "ltr",
                    "size": (11 if a.value != "signature" else 20) * rng.uniform(0.9, 1.15),
                    "dx": abs(jitter(3)) + 2,
                    "dy": rng.uniform(1.0, 2.5),
                    "rot": rng.uniform(-1, 1) * hand.tilt / 3,
                }
            )
    return items


def filled_pages(
    rendered: Rendered,
    answers: SheetAnswers,
    rng: random.Random,
    *,
    sheet_index: int = 0,
    ppi: float = 200,
    hand: Hand | None = None,
) -> list[bytes]:
    """PNG pages of one sheet with `answers` written in."""
    hand = hand or random_hand(rng)
    items = fill_items(rendered.layout, answers, hand, rng)
    return render_png(rendered, ppi, sheet_index=sheet_index, fill=items, extra_fonts=[FONTS])
