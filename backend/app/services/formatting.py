"""Human-readable number formatting for executive answers (ru / en)."""

from __future__ import annotations

import math

MINUS = "−"


def _group(value: float, decimals: int, lang: str) -> str:
    text = f"{abs(value):,.{decimals}f}"
    if lang == "ru":
        text = text.replace(",", " ").replace(".", ",")
    return (MINUS if value < 0 and lang == "ru" else "-" if value < 0 else "") + text


def _compact(value: float, lang: str) -> tuple[float, str]:
    size = abs(value)
    if lang == "ru":
        steps = ((1e9, "млрд"), (1e6, "млн"), (1e3, "тыс."))
    else:
        steps = ((1e9, "B"), (1e6, "M"), (1e3, "K"))
    for threshold, suffix in steps:
        if size >= threshold:
            return value / threshold, suffix
    return value, ""


def _decimals_for(value: float) -> int:
    """412,3 млн · 48,2 млн · 1,23 млрд."""
    return 1 if abs(value) >= 10 else 2


def format_value(value: float | int | None, unit: str | None, lang: str = "ru", currency_symbol: str = "₸") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    number = float(value)
    if unit == "percent":
        return f"{_group(number, 1, lang)} %" if lang == "ru" else f"{_group(number, 1, lang)}%"
    if unit == "currency":
        scaled, suffix = _compact(number, lang)
        decimals = _decimals_for(scaled) if suffix else 0
        if lang == "ru":
            body = _group(scaled, decimals, lang)
            return f"{body} {suffix} {currency_symbol}" if suffix else f"{body} {currency_symbol}"
        body = _group(scaled, decimals, lang)
        sign = "-" if body.startswith("-") else ""
        return f"{sign}{currency_symbol}{body.lstrip('-')}{suffix}"
    if unit == "hours":
        return f"{_group(number, 1, lang)} ч" if lang == "ru" else f"{_group(number, 1, lang)} h"
    if unit in ("liters", "kg"):
        scaled, suffix = _compact(number, lang)
        label = {"liters": "л" if lang == "ru" else "L", "kg": "кг" if lang == "ru" else "kg"}[unit]
        decimals = 0 if not suffix else 1
        return f"{_group(scaled, decimals, lang)}{' ' + suffix if suffix else ''} {label}"
    if float(number).is_integer():
        return _group(number, 0, lang)
    return _group(number, 2 if abs(number) < 10 else 1, lang)


def format_change_pct(change_pct: float | None, lang: str = "ru") -> str:
    if change_pct is None:
        return "—"
    sign = "+" if change_pct > 0 else ""
    body = _group(change_pct, 1, lang)
    return f"{sign}{body} %" if lang == "ru" else f"{sign}{body}%"


def direction(change_pct: float | None) -> str | None:
    if change_pct is None:
        return None
    if abs(change_pct) < 0.05:
        return "flat"
    return "up" if change_pct > 0 else "down"


def sentiment(change_pct: float | None, better: str) -> str:
    move = direction(change_pct)
    if move in (None, "flat"):
        return "neutral"
    good = (move == "up") == (better == "higher")
    return "positive" if good else "negative"


def change_pct(value: float | None, compare: float | None) -> float | None:
    if value is None or compare is None or compare == 0:
        return None
    return round((value - compare) / abs(compare) * 100, 2)
