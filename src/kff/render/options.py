"""Print options (the plan's print profile) and the fixed page geometry."""

from dataclasses import asdict, dataclass
from typing import Any

from kff.errors import KffError

# Width × height in mm, by Typst paper name.
PAPERS = {
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "a3": (297.0, 420.0),
    "us-letter": (215.9, 279.4),
    "us-legal": (215.9, 355.6),
}


@dataclass(frozen=True)
class RenderOptions:
    paper: str = "a4"
    language: str | None = None
    font_size: float = 10.0
    repeat_rows: int = 3
    show_hints: bool = True
    show_numbers: bool = True
    show_conditions: bool = True
    show_names: bool = False

    def __post_init__(self) -> None:
        if self.paper not in PAPERS:
            raise KffError(f"Unknown paper {self.paper!r}; use one of {', '.join(PAPERS)}.")
        if not 7 <= self.font_size <= 16:
            raise KffError("font size must be between 7 and 16 pt.")
        if not 1 <= self.repeat_rows <= 50:
            raise KffError("repeat rows must be between 1 and 50.")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Geometry:
    """Sizes in mm. Corner markers sit `marker_offset` in from each corner, inside the
    area most printers can reach (~5 mm)."""

    marker_size: float = 9.0
    marker_offset: float = 7.0
    qr_size: float = 22.0
    qr_gap: float = 3.0
    margin_x: float = 17.0
    margin_bottom: float = 22.0
    cell_w: float = 6.0
    cell_h: float = 7.5
    cell_gap: float = 1.0
    bubble: float = 4.2
    line_h: float = 8.0
    number_col: float = 9.0

    @property
    def margin_top(self) -> float:
        return self.marker_offset + max(self.marker_size, self.qr_size) + 5

    def text_cells(self, paper: str) -> int:
        """Comb cells that fit across the answer column (with room for a repeat's indent)."""
        width = PAPERS[paper][0] - 2 * self.margin_x - self.number_col - 4
        return int((width + self.cell_gap) // (self.cell_w + self.cell_gap))

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "margin_top": self.margin_top}
