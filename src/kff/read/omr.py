"""Optical mark reading: which bubbles and boxes are marked.

The signal is the share of the bubble's inside covered by ink. A filled bubble reads
~0.9, a cross ~0.3, an empty one ~0. Between "clearly marked" and "clearly empty" a mark
is doubtful (a faint tick, a smudge, an erasure) and the answer goes to review.
"""

from dataclasses import dataclass

import numpy as np

from kff.read.digits import ink_mask

MARKED = 0.18
EMPTY = 0.06


def fill(crop: np.ndarray) -> float:
    if crop.size == 0:
        return 0.0
    return float((ink_mask(crop) > 0.45).mean())


@dataclass
class ChoiceReading:
    selected: list[str]
    confidence: float
    fills: dict[str, float]
    reason: str | None = None


def read_choices(fills: dict[str, float], multiple: bool) -> ChoiceReading:
    """`fills` is choice value → fill, in form order."""
    marked = [v for v, f in fills.items() if f >= MARKED]
    doubtful = [v for v, f in fills.items() if EMPTY <= f < MARKED]

    def margin(f: float) -> float:
        # 1 at the clear ends, 0 at a threshold.
        if f >= MARKED:
            return min(1.0, (f - MARKED) / 0.12)
        return min(1.0, (EMPTY - f) / EMPTY) if f < EMPTY else 0.0

    confidence = min((margin(f) for f in fills.values()), default=1.0)
    confidence = 0.5 + 0.5 * confidence
    reason = None
    if doubtful:
        confidence = min(confidence, 0.5)
        reason = f"faint or partial mark on {', '.join(doubtful)}"
    if not multiple and len(marked) > 1:
        ranked = sorted(marked, key=lambda v: -fills[v])
        if fills[ranked[0]] > 2.5 * fills[ranked[1]]:
            # One filled, one crossed out or brushed: take the filled one, but check.
            marked = [ranked[0]]
            confidence = min(confidence, 0.6)
            reason = f"more than one marked; took {ranked[0]}"
        else:
            return ChoiceReading(marked, 0.2, fills, "more than one choice marked")
    return ChoiceReading(marked, confidence, fills, reason)
