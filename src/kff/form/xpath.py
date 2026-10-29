"""A small evaluator for the XPath that XLSForm skip logic and constraints usually use.

Covers `${name}` references, `.`, literals, `and`/`or`, comparisons, arithmetic and the
common functions (`selected`, `count-selected`, `not`, `string-length`, `if`,
`coalesce`, `regex`, `today`, …). Anything else (paths, `instance()`, `pulldata`,
unknown functions) raises `Unsupported`, and callers treat the rule as unknown rather than
guess: a question whose relevance can't be evaluated is kept.
"""

import math
import re
from collections.abc import Callable
from datetime import date
from typing import Any

Resolver = Callable[[str], Any]


class Unsupported(Exception):
    """The expression uses XPath this evaluator doesn't cover."""


_TOKEN = re.compile(
    r"""\s*(?:
      (?P<num>\d+(?:\.\d*)?|\.\d+)
    | (?P<str>'[^']*'|"[^"]*")
    | (?P<ref>\$\{[^}]+\})
    | (?P<op>!=|<=|>=|=|<|>|\+|-|\*|\(|\)|,)
    | (?P<dot>\.)
    | (?P<name>[A-Za-z_][\w.:-]*)
    | (?P<bad>\S)
    )""",
    re.VERBOSE,
)


def _tokens(expr: str) -> list[tuple[str, str]]:
    out = []
    for m in _TOKEN.finditer(expr):
        kind = m.lastgroup
        if kind is None:
            continue
        value = m.group(kind)
        if kind == "bad" or (kind == "op" and value == "/"):
            raise Unsupported(f"unexpected {value!r}")
        out.append((kind, value))
    return out


class _Parser:
    def __init__(self, expr: str, resolve: Resolver, current: Any) -> None:
        self.toks = _tokens(expr)
        self.pos = 0
        self.resolve = resolve
        self.current = current

    def peek(self, *values: str) -> bool:
        if self.pos >= len(self.toks):
            return False
        kind, value = self.toks[self.pos]
        return value in values and kind in ("op", "name")

    def take(self) -> tuple[str, str]:
        if self.pos >= len(self.toks):
            raise Unsupported("unexpected end")
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def expect(self, value: str) -> None:
        if self.take()[1] != value:
            raise Unsupported(f"expected {value!r}")

    def parse(self) -> Any:
        value = self.or_()
        if self.pos != len(self.toks):
            raise Unsupported(f"unexpected {self.toks[self.pos][1]!r}")
        return value

    def or_(self) -> Any:
        left = self.and_()
        while self.peek("or"):
            self.take()
            right = self.and_()
            left = truthy(left) or truthy(right)
        return left

    def and_(self) -> Any:
        left = self.cmp()
        while self.peek("and"):
            self.take()
            right = self.cmp()
            left = truthy(left) and truthy(right)
        return left

    def cmp(self) -> Any:
        left = self.add()
        while self.peek("=", "!=", "<", "<=", ">", ">="):
            op = self.take()[1]
            left = compare(left, op, self.add())
        return left

    def add(self) -> Any:
        left = self.mul()
        while self.peek("+", "-"):
            op = self.take()[1]
            a, b = number(left), number(self.mul())
            left = a + b if op == "+" else a - b
        return left

    def mul(self) -> Any:
        left = self.unary()
        while self.peek("*", "div", "mod"):
            op = self.take()[1]
            a, b = number(left), number(self.unary())
            if op == "*":
                left = a * b
            elif b == 0:
                left = math.nan
            else:
                left = a / b if op == "div" else math.fmod(a, b)
        return left

    def unary(self) -> Any:
        if self.peek("-"):
            self.take()
            return -number(self.unary())
        return self.primary()

    def primary(self) -> Any:
        kind, value = self.take()
        if kind == "num":
            return float(value)
        if kind == "str":
            return value[1:-1]
        if kind == "ref":
            v = self.resolve(value[2:-1].strip())
            return "" if v is None else v
        if kind == "dot":
            return "" if self.current is None else self.current
        if kind == "op" and value == "(":
            v = self.or_()
            self.expect(")")
            return v
        if kind == "name" and self.peek("("):
            self.take()
            args = []
            if not self.peek(")"):
                args.append(self.or_())
                while self.peek(","):
                    self.take()
                    args.append(self.or_())
            self.expect(")")
            return call(value, args)
        raise Unsupported(f"unexpected {value!r}")


def number(v: Any) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return math.nan


def string(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return "" if v is None else str(v)


def truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return not (v == 0 or math.isnan(v))
    return bool(v)


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def compare(a: Any, op: str, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        x, y = truthy(a), truthy(b)
        return {"=": x == y, "!=": x != y}.get(op, False)
    if op in ("=", "!="):
        if isinstance(a, float) or isinstance(b, float):
            equal = number(a) == number(b)
        else:
            equal = string(a) == string(b)
        return equal if op == "=" else not equal
    x, y = number(a), number(b)
    if math.isnan(x) or math.isnan(y):
        # Dates compare as text (ISO dates sort correctly).
        if _DATE.match(string(a)) and _DATE.match(string(b)):
            x, y = string(a), string(b)  # type: ignore[assignment]
        else:
            return False
    return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


def call(name: str, args: list[Any]) -> Any:
    def arg(i: int) -> Any:
        return args[i] if i < len(args) else ""

    match name:
        case "selected":
            return string(arg(1)) in string(arg(0)).split()
        case "count-selected":
            return float(len(string(arg(0)).split()))
        case "not":
            return not truthy(arg(0))
        case "true":
            return True
        case "false":
            return False
        case "boolean":
            return truthy(arg(0))
        case "string":
            return string(arg(0))
        case "number" | "int":
            n = number(arg(0))
            return float(math.trunc(n)) if name == "int" and not math.isnan(n) else n
        case "string-length":
            return float(len(string(arg(0))))
        case "concat":
            return "".join(string(a) for a in args)
        case "contains":
            return string(arg(1)) in string(arg(0))
        case "starts-with":
            return string(arg(0)).startswith(string(arg(1)))
        case "regex":
            return re.search(string(arg(1)), string(arg(0))) is not None
        case "if":
            return arg(1) if truthy(arg(0)) else arg(2)
        case "coalesce":
            return arg(0) if string(arg(0)) != "" else arg(1)
        case "round":
            digits = int(number(arg(1))) if len(args) > 1 else 0
            return float(round(number(arg(0)), digits))
        case "today":
            return date.today().isoformat()
    raise Unsupported(f"function {name}()")


def evaluate(expr: str, resolve: Resolver, current: Any = None) -> Any:
    """The value of `expr`. `resolve(name)` gives `${name}`; `current` is `.`."""
    return _Parser(expr, resolve, current).parse()


def holds(expr: Any, resolve: Resolver, current: Any = None) -> bool | None:
    """Whether a rule holds: True/False, or None when it can't be evaluated."""
    if not isinstance(expr, str) or not expr.strip():
        return True
    try:
        return truthy(evaluate(expr, resolve, current))
    except Unsupported:
        return None
