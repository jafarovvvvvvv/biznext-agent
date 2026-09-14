"""Checks that numbers in the agent's text appear in real query results (reported in the trace, not blocking)."""

from __future__ import annotations

import re

_NUMBER = re.compile(
    r"(?<![\w.,])(?P<sign>[−\-+])?(?P<num>\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?:[   ]?(?P<suffix>млрд|млн|тыс\.?|bn|B\b|M\b|K\b|%|п\.\s?п\.|pp\b))?",
    re.IGNORECASE,
)
_DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_SCALE = {"млрд": 1e9, "bn": 1e9, "b": 1e9, "млн": 1e6, "m": 1e6, "тыс": 1e3, "тыс.": 1e3, "k": 1e3}


def extract_numbers(text: str) -> list[tuple[str, float, float]]:
    """(original text, value, tolerance) for numbers worth verifying."""
    cleaned = _DATE.sub(" ", text)
    found: list[tuple[str, float, float]] = []
    for match in _NUMBER.finditer(cleaned):
        raw = match.group("num")
        suffix = (match.group("suffix") or "").lower().replace(" ", "")
        compact = re.sub(r"[   ]", "", raw)
        if re.fullmatch(r"\d{1,3}(,\d{3})+", compact):
            compact = compact.replace(",", "")
        decimals = len(re.split(r"[.,]", compact)[1]) if re.search(r"[.,]", compact) else 0
        value = float(compact.replace(",", "."))
        if match.group("sign") in ("−", "-"):
            value = -value
        scale = _SCALE.get(suffix.rstrip("."), _SCALE.get(suffix, 1.0))
        if scale == 1.0 and not suffix:
            if decimals == 0 and (abs(value) <= 31 or 1990 <= abs(value) <= 2100):
                continue  # day numbers, small counts, years
        tolerance = 0.5 * 10 ** (-decimals) * scale * 1.001 + 1e-9
        found.append((match.group(0).strip(), value * scale, tolerance))
    return found


def unverified_numbers(texts: list[str], values: list[float]) -> list[str]:
    candidates = set()
    for v in values:
        candidates.add(v)
        candidates.add(abs(v))
    ordered = sorted(candidates)
    missing: list[str] = []
    for text in texts:
        for original, value, tolerance in extract_numbers(text or ""):
            if not _matches(abs(value), tolerance, ordered) and original not in missing:
                missing.append(original)
    return missing


def _matches(value: float, tolerance: float, ordered: list[float]) -> bool:
    import bisect

    index = bisect.bisect_left(ordered, value - tolerance)
    return index < len(ordered) and ordered[index] <= value + tolerance
