from __future__ import annotations

from collections.abc import Iterable

from src.Infrastructure.process_utils import ProcessMatch, find_process_by_substrings


DEFAULT_QQ_PROCESS_MATCH = "qqmusic"


def _normalize_fragment(value: object) -> str:
    fragment = str(value or "").strip().lower()
    if fragment.endswith(".exe"):
        fragment = fragment[:-4]
    return fragment


def iter_qq_process_fragments(process_match: object = None) -> tuple[str, ...]:
    raw_values: Iterable[object]
    if isinstance(process_match, str):
        raw_values = process_match.replace("|", ",").replace(";", ",").split(",")
    elif isinstance(process_match, Iterable) and not isinstance(process_match, (str, bytes, bytearray)):
        raw_values = process_match
    else:
        raw_values = [process_match]

    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        fragment = _normalize_fragment(value)
        if not fragment or fragment in seen:
            continue
        seen.add(fragment)
        normalized.append(fragment)
    if not normalized:
        return (DEFAULT_QQ_PROCESS_MATCH,)
    return tuple(normalized)


def find_qqmusic_process(process_match: object = None) -> ProcessMatch | None:
    return find_process_by_substrings(iter_qq_process_fragments(process_match))
