"""Fuzzy matching of a messy file/folder name against provider search results."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from difflib import SequenceMatcher
from typing import TypeVar

T = TypeVar("T")

MIN_SCORE = 0.6
GOOD_ENOUGH = 0.9
MAX_TRUNCATIONS = 3
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_YEAR = re.compile(r"19\d{2}|20[0-2]\d")  # a release year, not "2077"


def tokens(text: str) -> list[str]:
    return [t for t in _NON_ALNUM.split(text.lower()) if t]


def title_score(query: str, title: str) -> float:
    """0..1 similarity: the better of token-set F1 (extra words on either side
    cost a little, a missing title word costs a lot) and character similarity.
    A year the query ends with ("Doom 1993") counts for a result that has it and costs nothing
    to one that does not: a provider's title rarely carries the year."""
    q = tokens(query)
    score = _title_score(q, tokens(title))
    if len(q) > 1 and _YEAR.fullmatch(q[-1]) and q[-1] not in tokens(title):
        score = max(score, _title_score(q[:-1], tokens(title)))
    return score


def _title_score(q: list[str], t: list[str]) -> float:
    if not q or not t:
        return 0.0
    common = len(set(q) & set(t))
    precision, recall = common / len(set(q)), common / len(set(t))
    f1 = 2 * precision * recall / (precision + recall) if common else 0.0
    return max(f1, SequenceMatcher(None, " ".join(q), " ".join(t)).ratio())


_TRAILING_YEAR = re.compile(r"\s*\(\s*(?:19\d{2}|20[0-2]\d)\s*\)\s*$")


def query_variants(names: Iterable[str]) -> list[str]:
    """Distinct search strings: each name as is (without the parentheses around a year, which a provider's search
    does not read), then without that year, then with trailing words dropped one at a time (a leftover release
    group or tag usually trails)."""
    seen: list[str] = []
    for name in names:
        plain = _TRAILING_YEAR.sub("", name)
        for form in (name, plain) if plain != name else (name,):
            words = re.sub(r"[()]", " ", form).split()
            for drop in range(MAX_TRUNCATIONS + 1):
                if len(words) - drop < 2 and drop > 0:
                    break
                variant = " ".join(words[: len(words) - drop])
                if variant and variant not in seen:
                    seen.append(variant)
    return seen


def best_match(
    queries: Iterable[str],
    reference: str,
    search: Callable[[str], list[T]],
    name_of: Callable[[T], str],
) -> T | None:
    """Search each query and return the result whose name best matches
    `reference` (the cleaned name), or None if nothing reaches MIN_SCORE.
    Stops early once a result is good enough."""
    best: T | None = None
    best_score = 0.0
    for query in queries:
        for result in search(query):
            score = title_score(reference, name_of(result))
            if score > best_score:
                best, best_score = result, score
        if best_score >= GOOD_ENOUGH:
            break
    return best if best_score >= MIN_SCORE else None
