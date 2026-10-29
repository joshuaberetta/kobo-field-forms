"""The script registry (`scripts.toml`): fonts, direction, digits and text entry per script."""

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

REGISTRY = Path(__file__).with_name("scripts.toml")
DEFAULT = "latin"


@dataclass(frozen=True)
class Script:
    name: str
    languages: tuple[str, ...]
    fonts: tuple[str, ...]
    direction: Literal["ltr", "rtl"]
    digits: tuple[str, ...]
    text_entry: Literal["comb", "lines"]


@cache
def scripts() -> dict[str, Script]:
    data = tomllib.loads(REGISTRY.read_text())
    return {
        name: Script(
            name=name,
            languages=tuple(entry["languages"]),
            fonts=tuple(entry["fonts"]),
            direction=entry["direction"],
            digits=tuple(entry["digits"]),
            text_entry=entry["text_entry"],
        )
        for name, entry in data.items()
    }


def script_for(lang: str | None) -> tuple[Script, bool]:
    """The script a language is written in, and whether the registry knows the language."""
    for script in scripts().values():
        if lang and lang.lower() in script.languages:
            return script, True
    return scripts()[DEFAULT], False


def digit_map() -> dict[str, str]:
    """Every registered digit → its ASCII digit (`٣` → `3`)."""
    return {
        ch: str(i) for s in scripts().values() for digits in s.digits for i, ch in enumerate(digits)
    }
