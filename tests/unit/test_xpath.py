import pytest

from kff.form.xpath import Unsupported, evaluate, holds

ANSWERS = {"age": "34", "consent": "yes", "assets": "phone tv", "name": "Ann", "d": "2026-05-01"}


def resolve(name):
    return ANSWERS.get(name)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("${consent} = 'yes'", True),
        ("${consent} != 'yes'", False),
        ("${age} >= 18 and ${age} < 65", True),
        ("${age} > 40 or selected(${assets}, 'tv')", True),
        ("not(selected(${assets}, 'radio'))", True),
        ("count-selected(${assets}) = 2", True),
        ("${missing} = ''", True),
        ("string-length(${name}) > 2", True),
        ("${age} + 1 = 35", True),
        ("${age} div 2 = 17", True),
        ("${age} mod 5 = 4", True),
        ("-${age} < 0", True),
        ("if(${age} > 18, 'adult', 'child') = 'adult'", True),
        ("coalesce(${missing}, 'x') = 'x'", True),
        ("regex(${name}, '^[A-Z]')", True),
        ("${d} < '2026-06-01'", True),
        ("(${age} > 50) or (${consent} = 'yes')", True),
    ],
)
def test_evaluate(expr, expected):
    assert holds(expr, resolve) is expected


def test_dot_is_the_current_value():
    assert holds(". >= 0 and . <= 120", resolve, "121") is False
    assert holds(". >= 0 and . <= 120", resolve, "7") is True
    assert evaluate(".", resolve, "x") == "x"


@pytest.mark.parametrize(
    "expr", ["/data/age > 3", "pulldata('x', 'y')", "instance('c')/root/item", "${age} >"]
)
def test_unsupported(expr):
    with pytest.raises(Unsupported):
        evaluate(expr, resolve)
    assert holds(expr, resolve) is None


def test_empty_rule_holds():
    assert holds("", resolve) is True
    assert holds(None, resolve) is True
