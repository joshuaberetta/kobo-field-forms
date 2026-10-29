"""Form content → the printable model: sections and questions, each with a paper widget.

The model is plain data (dataclasses → JSON) that the Typst template draws. Every answer
area gets a stable field id: the question's xpath, with the repeat instance in brackets
inside repeats (`members[2]/name`). The scan side uses the same ids.

Row handling follows kobo-mcp's `formcontent/openrosa.parse` and `describe.py`.
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Kobo writes both spellings (`begin_group` and `begin group`).
_OPENERS = {"begin_group": "group", "begin_repeat": "repeat", "begin_score": "group"}
_CLOSERS = {"end_group", "end_repeat", "end_score", "end_rank", "end_kobomatrix"}
# Score blocks deploy as a group of select_ones over a list named on the begin row.
_SCORE_LIST = "kobo--score-choices"
# Rows with nothing for a person to answer: not printed, not reported.
_SILENT_TYPES = {
    "calculate",
    "hidden",
    "start",
    "end",
    "today",
    "deviceid",
    "username",
    "phonenumber",
    "simserial",
    "subscriberid",
    "audit",
    "start-geopoint",
    "xml-external",
    "csv-external",
}
# Rows that can't be answered on paper: printed as a note, reported as a warning.
_UNSUPPORTED_TYPES = {
    "audio": "audio recording",
    "background-audio": "audio recording",
    "video": "video",
    "file": "file upload",
    "geotrace": "line on a map",
    "geoshape": "shape on a map",
    "select_one_from_file": "choice list from a file",
    "select_multiple_from_file": "choice list from a file",
    "begin_rank": "ranking",
    "begin_kobomatrix": "matrix",
}
_TAG = re.compile(r"<[^>]+>")
_REF = re.compile(r"\$\{([^}]+)\}")
_TRUE = {True, "true", "yes", "TRUE", "True", "Yes", "true()"}

Charset = Literal["digit", "text", "sign"]


@dataclass
class Segment:
    """`cells` boxes in a row. `sep` is printed after it (".", "/", ":"); `caption` under
    it ("DD")."""

    cells: int
    charset: Charset = "digit"
    sep: str | None = None
    caption: str | None = None


@dataclass
class CombRow:
    segments: list[Segment]
    label: str | None = None


@dataclass
class Choice:
    value: str
    label: str


@dataclass
class Widget:
    kind: Literal["choices", "comb", "lines", "box", "none"]
    multiple: bool = False
    layout: Literal["horizontal", "vertical", "columns"] = "vertical"
    choices: list[Choice] = field(default_factory=list)
    rows: list[CombRow] = field(default_factory=list)
    columns: int = 1
    lines: int = 0
    height_mm: float = 0
    note: str | None = None


@dataclass
class Question:
    id: str
    xpath: str
    name: str
    type: str
    number: int
    label: str
    hint: str | None
    required: bool
    condition: str | None
    widget: Widget
    kind: str = "question"


@dataclass
class Note:
    id: str
    label: str
    hint: str | None = None
    condition: str | None = None
    kind: str = "note"


@dataclass
class Section:
    id: str
    xpath: str
    kind: Literal["group", "repeat"]
    label: str | None
    condition: str | None
    children: list[Any] = field(default_factory=list)
    # Repeats only: this instance's number and how many are printed.
    instance: int | None = None
    instances: int | None = None


@dataclass
class PrintModel:
    title: str
    language: str
    language_index: int
    items: list[Any]
    warnings: list[str]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def questions(self) -> list[Question]:
        out: list[Question] = []

        def walk(items: list[Any]) -> None:
            for item in items:
                if isinstance(item, Question):
                    out.append(item)
                elif isinstance(item, Section):
                    walk(item.children)

        walk(self.items)
        return out


@dataclass
class ModelOptions:
    language: str | None = None
    # Rows printed for a repeat without a fixed `repeat_count`.
    repeat_rows: int = 3
    # Comb cells per row for a full-width text answer (set from the page width).
    text_cells: int = 24
    lines: int = 4


# -- content helpers ---------------------------------------------------------


def norm_type(raw: Any) -> tuple[str, str | None]:
    """`"select_one yn"` → ("select_one", "yn"); `"begin group"` → ("begin_group", None)."""
    text = str(raw or "").strip()
    if text in ("begin group", "end group", "begin repeat", "end repeat"):
        return text.replace(" ", "_"), None
    head, _, rest = text.partition(" ")
    return head, (rest.strip() or None)


def translations(content: dict[str, Any]) -> list[str | None]:
    value = content.get("translations")
    return list(value) if isinstance(value, list) and value else [None]


def pick_language(content: dict[str, Any], requested: str | None) -> int:
    """A translation index from a name (`Arabic (ar)`), code (`ar`) or bare name
    (`Arabic`). Default: the form's `default_language`, else the first."""
    names = translations(content)
    settings = content.get("settings")
    if isinstance(settings, list):
        settings = settings[0] if settings else {}
    wanted = requested or (settings or {}).get("default_language")
    if wanted:
        w = str(wanted).strip().lower()
        for i, name in enumerate(names):
            if name is None:
                continue
            n = name.lower()
            code = re.search(r"\(([^)]+)\)\s*$", n)
            if w in (n, n.split(" (")[0]) or (code and code.group(1) == w):
                return i
        if requested:
            shown = ", ".join(n or "(default)" for n in names)
            raise ValueError(f"The form has no language {requested!r}. It has: {shown}.")
    return 0


def _text(value: Any, index: int) -> str | None:
    """A translatable cell in one language, falling back to the first non-empty one."""
    if isinstance(value, list):
        picked = value[index] if index < len(value) else None
        if not picked:
            picked = next((v for v in value if v), None)
        value = picked
    if value is None or value == "":
        return None
    text = _TAG.sub("", str(value)).strip()
    return text or None


def _max_digits(constraint: Any, default: int) -> int:
    """Digits for the largest value a constraint allows (`. <= 120` → 3)."""
    if not isinstance(constraint, str):
        return default
    # A strict bound (`. < 100`) allows one less: 99 needs 2 digits, not 3.
    bounds = [
        float(m.group(2)) - (m.group(1) == "<")
        for m in re.finditer(r"\.\s*(<=|<)\s*(\d+(?:\.\d+)?)", constraint)
    ]
    bounds += [
        float(m.group(1)) - (m.group(2) == ">")
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(>=|>)\s*\.(?![\d])", constraint)
    ]
    if not bounds:
        return default
    return max(1, len(str(int(max(bounds)))))


def _range_digits(parameters: Any, default: int) -> int:
    m = re.search(r"end\s*=\s*(-?\d+(?:\.\d+)?)", str(parameters or ""))
    return max(1, len(str(int(abs(float(m.group(1))))))) if m else default


# -- relevance in plain words ------------------------------------------------


class _Refs:
    """Resolves `${name}` to a question number and choice values to labels."""

    def __init__(self) -> None:
        self.numbers: dict[str, int] = {}
        self.choice_labels: dict[str, dict[str, str]] = {}

    def ref(self, name: str) -> str:
        number = self.numbers.get(name)
        return f"Q{number}" if number else f"“{name}”"

    def value(self, name: str, value: str) -> str:
        return f"“{self.choice_labels.get(name, {}).get(value, value)}”"


_OPS = {
    "!=": "is not",
    ">=": "is at least",
    "<=": "is at most",
    ">": "is more than",
    "<": "is less than",
    "=": "is",
}
_SELECTED = re.compile(r"(not\s*\(\s*)?selected\(\s*\$\{([^}]+)\}\s*,\s*'([^']*)'\s*\)(\s*\))?")
_COMPARE = re.compile(r"\$\{([^}]+)\}\s*(!=|>=|<=|=|>|<)\s*('([^']*)'|-?\d+(?:\.\d+)?)")


def describe_condition(expr: Any, refs: _Refs) -> str | None:
    """`selected(${a}, 'x') and ${b} > 3` → `Q1 includes “X” and Q2 is more than 3`.

    Approximate on purpose: common patterns become words, anything else keeps its
    syntax with references replaced by question numbers.
    """
    if not isinstance(expr, str) or not expr.strip():
        return None

    def selected(m: re.Match[str]) -> str:
        negated = bool(m.group(1)) and bool(m.group(4))
        verb = "doesn't include" if negated else "includes"
        tail = "" if negated or not m.group(4) else m.group(4)
        return f"{refs.ref(m.group(2))} {verb} {refs.value(m.group(2), m.group(3))}{tail}"

    def compare(m: re.Match[str]) -> str:
        name, op = m.group(1), m.group(2)
        if m.group(4) is not None and m.group(4) == "" and op in ("=", "!="):
            return f"{refs.ref(name)} {'is blank' if op == '=' else 'is answered'}"
        rhs = refs.value(name, m.group(4)) if m.group(4) is not None else m.group(3)
        return f"{refs.ref(name)} {_OPS[op]} {rhs}"

    text = _SELECTED.sub(selected, expr)
    text = _COMPARE.sub(compare, text)
    text = _REF.sub(lambda m: refs.ref(m.group(1)), text)
    return re.sub(r"\s+", " ", text).strip()


# -- building ----------------------------------------------------------------


@dataclass
class _Node:
    row: dict[str, Any]
    type: str
    arg: str | None
    name: str
    xpath: str
    children: list["_Node"] = field(default_factory=list)


def _tree(content: dict[str, Any]) -> list[_Node]:
    root = _Node({}, "group", None, "", "")
    stack = [root]
    score_list: str | None = None
    for row in content.get("survey") or []:
        if not isinstance(row, dict):
            continue
        rtype, arg = norm_type(row.get("type"))
        if rtype in _CLOSERS:
            if len(stack) > 1:
                stack.pop()
            continue
        name = str(row.get("name") or row.get("$autoname") or "")
        if not name:
            continue
        parent = stack[-1]
        xpath = f"{parent.xpath}/{name}" if parent.xpath else name
        if rtype == "begin_score":
            score_list = row.get(_SCORE_LIST)
        if rtype == "score__row":
            rtype, arg = "select_one", score_list
        node = _Node(row, _OPENERS.get(rtype, rtype), arg, name, xpath)
        parent.children.append(node)
        if rtype in _OPENERS or rtype in ("begin_rank", "begin_kobomatrix"):
            stack.append(node)
    return root.children


def build_model(
    content: dict[str, Any], title: str, options: ModelOptions | None = None
) -> PrintModel:
    opts = options or ModelOptions()
    lang = pick_language(content, opts.language)
    names = translations(content)
    warnings: list[str] = []

    choices: dict[str, list[Choice]] = {}
    for row in content.get("choices") or []:
        list_name = row.get("list_name")
        value = row.get("name") if row.get("name") is not None else row.get("$autovalue")
        if list_name and value is not None:
            label = _text(row.get("label"), lang) or str(value)
            choices.setdefault(str(list_name), []).append(Choice(str(value), label))

    tree = _tree(content)
    refs = _Refs()

    # Number the questions first, so conditions can refer to later ones too.
    counter = 0

    def number(nodes: list[_Node]) -> None:
        nonlocal counter
        for node in nodes:
            if node.type in ("group", "repeat"):
                number(node.children)
            elif node.type not in _SILENT_TYPES and node.type != "note":
                if _is_calculated(node.row):
                    continue
                counter += 1
                refs.numbers[node.name] = counter
                list_name = node.row.get("select_from_list_name") or node.arg
                if list_name in choices:
                    refs.choice_labels[node.name] = {c.value: c.label for c in choices[list_name]}

    number(tree)

    def widget(node: _Node) -> Widget:
        row, t = node.row, node.type
        appearance = str(row.get("appearance") or "")
        if t in ("select_one", "select_multiple"):
            list_name = row.get("select_from_list_name") or node.arg
            options = choices.get(str(list_name), [])
            if not options:
                warnings.append(f"{node.xpath}: choice list {list_name!r} is empty or missing.")
            layout, columns = _choice_layout(options)
            return Widget(
                "choices",
                multiple=t == "select_multiple",
                layout=layout,
                columns=columns,
                choices=options,
            )
        if t == "acknowledge":
            return Widget("choices", choices=[Choice("OK", "OK")], layout="horizontal")
        if t == "integer":
            return _comb(Segment(_max_digits(row.get("constraint"), 4)))
        if t == "decimal":
            whole = _max_digits(row.get("constraint"), 4)
            return _comb(Segment(whole, sep="."), Segment(2))
        if t == "range":
            return _comb(Segment(_range_digits(row.get("parameters"), 3)))
        if t == "date":
            if "month-year" in appearance:
                return _comb(Segment(2, sep="/", caption="MM"), Segment(4, caption="YYYY"))
            if "year" in appearance:
                return _comb(Segment(4, caption="YYYY"))
            return _comb(*_date_segments())
        if t == "time":
            return _comb(*_time_segments())
        if t == "datetime":
            segs = _date_segments()
            return Widget("comb", rows=[CombRow(segs), CombRow(_time_segments())])
        if t == "geopoint":
            return Widget(
                "comb",
                rows=[
                    CombRow(
                        [Segment(1, "sign", caption="±"), Segment(2, sep="."), Segment(5)],
                        label="Lat",
                    ),
                    CombRow(
                        [Segment(1, "sign", caption="±"), Segment(3, sep="."), Segment(5)],
                        label="Long",
                    ),
                ],
            )
        if t == "text" and "numbers" in appearance:
            return _comb(Segment(opts.text_cells, "digit"))
        if t == "text" and "multiline" in appearance:
            return Widget("lines", lines=opts.lines)
        if t in ("text", "barcode"):
            return _comb(Segment(opts.text_cells, "text"))
        if t == "image":
            signature = any(a in appearance for a in ("signature", "draw", "annotate"))
            return Widget(
                "box",
                height_mm=30 if signature else 60,
                note=None if signature else "Draw or describe here.",
            )
        what = _UNSUPPORTED_TYPES.get(t, f"type {t!r}")
        warnings.append(f"{node.xpath}: {what} can't be answered on paper; left out.")
        return Widget("none", note=f"Not collected on paper ({what}).")

    def build(nodes: list[_Node], prefix: str) -> list[Any]:
        items: list[Any] = []
        for node in nodes:
            row = node.row
            item_id = f"{prefix}/{node.name}" if prefix else node.xpath
            label = _text(row.get("label"), lang)
            hint = _text(row.get("hint"), lang)
            condition = describe_condition(row.get("relevant"), refs)
            if node.type in _SILENT_TYPES or _is_calculated(row):
                continue
            if node.type == "group":
                items.append(
                    Section(
                        item_id,
                        node.xpath,
                        "group",
                        label,
                        condition,
                        build(node.children, item_id),
                    )
                )
            elif node.type == "repeat":
                count = _repeat_count(row.get("repeat_count"), opts.repeat_rows)
                for i in range(1, count + 1):
                    instance_id = f"{item_id}[{i}]"
                    items.append(
                        Section(
                            instance_id,
                            node.xpath,
                            "repeat",
                            label,
                            condition,
                            build(node.children, instance_id),
                            instance=i,
                            instances=count,
                        )
                    )
            elif node.type == "note":
                items.append(Note(item_id, label or "", hint, condition))
            else:
                w = widget(node)
                items.append(
                    Question(
                        id=item_id,
                        xpath=node.xpath,
                        name=node.name,
                        type=node.type,
                        number=refs.numbers.get(node.name, 0),
                        label=_REF.sub(lambda m: refs.ref(m.group(1)), label or node.name),
                        hint=hint,
                        required=row.get("required") in _TRUE,
                        condition=condition,
                        widget=w,
                    )
                )
        return items

    items = build(tree, "")
    language = names[lang] or ""
    return PrintModel(title, language, lang, items, warnings)


def _is_calculated(row: dict[str, Any]) -> bool:
    # A read-only question with a calculation shows a computed value: nothing to write.
    return bool(row.get("calculation")) and row.get("readonly") in _TRUE


def _repeat_count(value: Any, default: int) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(1, min(n, 50))


def _comb(*segments: Segment) -> Widget:
    return Widget("comb", rows=[CombRow(list(segments))])


def _date_segments() -> list[Segment]:
    return [
        Segment(2, sep="/", caption="DD"),
        Segment(2, sep="/", caption="MM"),
        Segment(4, caption="YYYY"),
    ]


def _time_segments() -> list[Segment]:
    return [Segment(2, sep=":", caption="HH"), Segment(2, caption="MM")]


def _choice_layout(
    options: list[Choice],
) -> tuple[Literal["horizontal", "vertical", "columns"], int]:
    """Short lists on one line; longer ones in 3 or 2 columns; long labels one per line."""
    longest = max((len(c.label) for c in options), default=0)
    if len(options) <= 5 and longest <= 14:
        return "horizontal", 1
    if longest <= 16:
        return "columns", 3
    if longest <= 30:
        return "columns", 2
    return "vertical", 1
