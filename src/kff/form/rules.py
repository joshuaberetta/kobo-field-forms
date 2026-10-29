"""Skip logic and constraints over answers keyed by field id (`hh/members[2]/age`).

`${name}` resolves to the question with that name; inside a repeat it means the same
instance as the question asking (`members[2]/age` → `members[2]/name`), as in Enketo.
"""

import re
from dataclasses import dataclass
from typing import Any

from kff.form.model import _tree
from kff.form.xpath import holds

_INDEX = re.compile(r"\[\d+\]")


def strip_index(field_id: str) -> str:
    """`hh/members[2]/age` → `hh/members/age`."""
    return _INDEX.sub("", field_id)


@dataclass
class Rules:
    rows: dict[str, dict[str, Any]]  # xpath → row, for questions, groups and repeats
    by_name: dict[str, str]  # question name → xpath
    repeats: set[str]  # xpaths of repeats

    def instance_id(self, xpath: str, context_id: str) -> str:
        """The id of `xpath` in the same repeat instances as `context_id`."""
        prefixes: list[tuple[str, str]] = []
        parts = context_id.split("/")
        for i in range(1, len(parts) + 1):
            indexed = "/".join(parts[:i])
            if indexed.endswith("]"):
                prefixes.append((strip_index(indexed), indexed))
        for plain, indexed in sorted(prefixes, key=lambda p: -len(p[0])):
            if xpath.startswith(plain + "/"):
                return indexed + xpath[len(plain) :]
        return xpath

    def resolver(self, answers: dict[str, Any], context_id: str):
        def resolve(name: str) -> Any:
            xpath = self.by_name.get(name)
            return None if xpath is None else answers.get(self.instance_id(xpath, context_id))

        return resolve

    def ancestors(self, field_id: str) -> list[str]:
        """Ids of the groups and repeat instances around a field, outermost first."""
        parts = field_id.split("/")
        return ["/".join(parts[:i]) for i in range(1, len(parts))]

    def relevant(self, field_id: str, answers: dict[str, Any]) -> bool:
        """Whether a field is shown, given the answers. Rules that can't be evaluated
        count as shown: better to keep an answer than to drop one wrongly."""
        for item in [*self.ancestors(field_id), field_id]:
            row = self.rows.get(strip_index(item), {})
            if holds(row.get("relevant"), self.resolver(answers, field_id)) is False:
                return False
        return True

    def constraint_ok(self, field_id: str, value: Any, answers: dict[str, Any]) -> bool | None:
        row = self.rows.get(strip_index(field_id), {})
        return holds(row.get("constraint"), self.resolver(answers, field_id), value)


def rules(content: dict[str, Any]) -> Rules:
    rows: dict[str, dict[str, Any]] = {}
    by_name: dict[str, str] = {}
    repeats: set[str] = set()

    def walk(nodes):
        for node in nodes:
            rows[node.xpath] = node.row
            by_name.setdefault(node.name, node.xpath)
            if node.type == "repeat":
                repeats.add(node.xpath)
            walk(node.children)

    walk(_tree(content))
    return Rules(rows, by_name, repeats)
