"""Print options (the plan's print profile) and the fixed page geometry."""

from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

from kff.errors import KffError

# Width × height in mm, portrait, by Typst paper name.
PAPERS = {
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "a3": (297.0, 420.0),
    "us-letter": (215.9, 279.4),
    "us-legal": (215.9, 355.6),
}
LANGUAGE_LAYOUTS = ("stacked", "columns", "per-sheet")
CHOICE_LAYOUTS = ("auto", "horizontal", "vertical", "columns")
TEXT_ENTRIES = ("auto", "comb", "lines")
# Narrowest a question column may be (mm), and the fewest letter boxes a text row may have.
MIN_COLUMN_MM = 80
MIN_TEXT_CELLS = 8


@dataclass(frozen=True)
class RenderOptions:
    paper: str = "a4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    sides: Literal["single", "double"] = "single"
    # Translation names or codes, page language first. Empty: the form's default.
    languages: tuple[str, ...] = ()
    language_layout: Literal["stacked", "columns", "per-sheet"] = "stacked"
    # Question columns per page.
    columns: int = 1
    font_size: float = 10.0
    choice_layout: Literal["auto", "horizontal", "vertical", "columns"] = "auto"
    text_entry: Literal["auto", "comb", "lines"] = "auto"
    repeat_rows: int = 3
    show_hints: bool = True
    show_numbers: bool = True
    show_conditions: bool = True
    show_instructions: bool = True
    show_names: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "languages", tuple(self.languages))
        if self.paper not in PAPERS:
            raise KffError(f"Unknown paper {self.paper!r}; use one of {', '.join(PAPERS)}.")
        if self.orientation not in ("portrait", "landscape"):
            raise KffError("orientation must be portrait or landscape.")
        if self.sides not in ("single", "double"):
            raise KffError("sides must be single or double.")
        if self.language_layout not in LANGUAGE_LAYOUTS:
            raise KffError(f"language layout must be one of {', '.join(LANGUAGE_LAYOUTS)}.")
        if self.choice_layout not in CHOICE_LAYOUTS:
            raise KffError(f"choice layout must be one of {', '.join(CHOICE_LAYOUTS)}.")
        if self.text_entry not in TEXT_ENTRIES:
            raise KffError(f"text entry must be one of {', '.join(TEXT_ENTRIES)}.")
        if self.columns not in (1, 2):
            raise KffError("columns must be 1 or 2.")
        if not 7 <= self.font_size <= 16:
            raise KffError("font size must be between 7 and 16 pt.")
        if not 1 <= self.repeat_rows <= 50:
            raise KffError("repeat rows must be between 1 and 50.")
        if len(self.languages) > 4:
            raise KffError("at most 4 languages per sheet.")
        if self.language_layout == "columns" and len(self.languages) > 3:
            raise KffError("at most 3 languages side by side.")

    @property
    def size(self) -> tuple[float, float]:
        w, h = PAPERS[self.paper]
        return (h, w) if self.orientation == "landscape" else (w, h)

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["languages"] = list(self.languages)
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RenderOptions":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class Geometry:
    """Sizes in mm. Corner markers sit `marker_offset` in from each corner, inside the
    area most printers can reach (~5 mm)."""

    marker_size: float = 9.0
    marker_offset: float = 7.0
    qr_size: float = 25.0
    qr_gap: float = 3.0
    margin_x: float = 17.0
    margin_bottom: float = 22.0
    # Extra inside margin on double-sided sheets, for binding or stapling.
    binding: float = 5.0
    column_gutter: float = 7.0
    cell_w: float = 6.0
    cell_h: float = 7.5
    cell_gap: float = 1.0
    bubble: float = 4.2
    line_h: float = 8.0
    number_col: float = 9.0

    @property
    def margin_top(self) -> float:
        return self.marker_offset + max(self.marker_size, self.qr_size) + 5

    def column_width(self, options: RenderOptions) -> float:
        width = options.size[0] - 2 * self.margin_x
        if options.sides == "double":
            width -= self.binding
        return (width - (options.columns - 1) * self.column_gutter) / options.columns

    def text_cells(self, options: RenderOptions) -> int:
        """Comb cells that fit across a question's answer area (room left for a repeat's
        indent)."""
        width = self.column_width(options) - self.number_col - 4
        return int((width + self.cell_gap) // (self.cell_w + self.cell_gap))

    def check(self, options: RenderOptions) -> None:
        """Refuse layouts that can't work, before rendering."""
        column = self.column_width(options)
        if column < MIN_COLUMN_MM:
            raise KffError(
                f"{options.columns} question columns on {options.paper} {options.orientation} "
                f"leave {column:.0f} mm each; use 1 column, landscape or bigger paper."
            )
        if self.text_cells(options) < MIN_TEXT_CELLS:
            raise KffError("Too narrow for text boxes; use fewer columns or bigger paper.")
        langs = len(options.languages)
        if options.language_layout == "columns" and langs > 1 and column / langs < 45:
            raise KffError(
                f"{langs} languages side by side leave {column / langs:.0f} mm each; use "
                "stacked, landscape, one question column or bigger paper."
            )

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "margin_top": self.margin_top}
