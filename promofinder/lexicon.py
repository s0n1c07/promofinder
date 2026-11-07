"""Word lists that drive the rule signals (lexicons/*.toml).

Each group is compiled into one case-insensitive regex. Terms match whole words or phrases;
a trailing * matches any word ending. Terms may end in punctuation ("what?"). A group may
set `kinds` to apply only to some block kinds (e.g. spoken lines only).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _flatten(data: dict, prefix: str = "") -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and "terms" in value:
            groups[name] = value
        elif isinstance(value, dict):
            groups.update(_flatten(value, name))
    return groups


def _term_pattern(term: str) -> str:
    term = term.strip().lower()
    if term.endswith("*"):
        return re.escape(term[:-1]) + r"[\w'-]*"
    return re.escape(term)


class Lexicon:
    def __init__(self, groups: dict[str, dict]):
        self.weights: dict[str, float] = {}
        self.terms: dict[str, list[str]] = {}
        self.kinds: dict[str, set[str] | None] = {}
        self._patterns: dict[str, re.Pattern] = {}
        self._starts: dict[str, re.Pattern] = {}
        for name, spec in groups.items():
            terms = [t for t in spec["terms"] if t.strip()]
            if not terms:
                raise ValueError(f"lexicon group {name!r} has no terms")
            weight = float(spec.get("weight", 1.0))
            if not 0 < weight <= 1:
                raise ValueError(f"lexicon group {name!r} weight must be in (0, 1]")
            # longest first, so "drops to one knee" wins over shorter overlapping terms
            body = "|".join(_term_pattern(t) for t in sorted(terms, key=len, reverse=True))
            self.weights[name] = weight
            self.terms[name] = terms
            self.kinds[name] = set(spec["kinds"]) if "kinds" in spec else None
            self._patterns[name] = re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.I)
            self._starts[name] = re.compile(rf"^\W*(?:{body})(?!\w)", re.I)

    @classmethod
    def load(cls, path: str | Path) -> "Lexicon":
        return cls(_flatten(tomllib.loads(Path(path).read_text(encoding="utf-8"))))

    def find(self, group: str, text: str, kind: str | None = None) -> list[str]:
        """Matched phrases (lowercase, in order) of one group; [] if the group skips this block kind."""
        allowed = self.kinds[group]
        if kind is not None and allowed is not None and kind not in allowed:
            return []
        return [m.group(0).lower() for m in self._patterns[group].finditer(text)]

    def starts_with(self, group: str, text: str) -> bool:
        return self._starts[group].match(text) is not None

    def strongest(self, prefix: str, text: str, kind: str | None = None) -> tuple[float, str | None, list[str], int]:
        """Across groups named prefix.*: (best weight, best group suffix, all matches, groups hit)."""
        best, best_group, found, hit = 0.0, None, [], 0
        for name in self._patterns:
            if not name.startswith(prefix + "."):
                continue
            matches = self.find(name, text, kind)
            if matches:
                hit += 1
                found += matches
                if self.weights[name] > best:
                    best, best_group = self.weights[name], name.split(".", 1)[1]
        return best, best_group, found, hit
