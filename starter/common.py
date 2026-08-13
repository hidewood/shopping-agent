"""Shared text-normalisation and deduplication utilities used across modules."""

from __future__ import annotations

import re
from typing import Any, Iterable


GENERIC_CONCEPT_WORDS = {
    "a", "an", "and", "about", "for", "from", "in", "item", "of", "on", "product",
    "related", "style", "styled", "suitable", "theme", "themed", "to", "with",
}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _strength(value: Any, default: str = "hard") -> str:
    text = str(value).strip().lower()
    return text if text in {"hard", "preference"} else default


def _normalize(value: str) -> str:
    """Case/punctuation-insensitive normalization that retains non-English letters."""
    return re.sub(r"[^\w]+", " ", value.casefold()).replace("_", " ").strip()


def _meaningful_tokens(value: str) -> set[str]:
    return {token for token in _normalize(value).split() if token not in GENERIC_CONCEPT_WORDS}


def _deduplicate(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        key = _normalize(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _deduplicate_tag_groups(groups: Iterable[Iterable[str]]) -> list[list[str]]:
    """Keep OR groups intact while removing repeated values and duplicate groups."""
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for group in groups:
        values = _deduplicate(str(value) for value in group)
        key = tuple(sorted(_normalize(value) for value in values))
        if values and key not in seen:
            seen.add(key)
            result.append(values)
    return result
