"""Deterministic calendar periods relative to 'today' in the application timezone.

All periods are half-open intervals [start, end).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
             "ноябрь", "декабрь"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]
QUARTERS_RU = ["I", "II", "III", "IV"]


@dataclass(frozen=True)
class Period:
    key: str
    start: date
    end: date  # exclusive
    label_ru: str
    label_en: str

    def label(self, lang: str) -> str:
        return self.label_en if lang == "en" else self.label_ru

    @property
    def last_day(self) -> date:
        return self.end - timedelta(days=1)

    @property
    def days(self) -> int:
        return (self.end - self.start).days


def today_in(timezone: str) -> date:
    return datetime.now(ZoneInfo(timezone)).date()


def add_months(d: date, months: int) -> date:
    year, month = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(year, month + 1, 1)


def month_label(d: date, lang: str) -> str:
    return f"{MONTHS_EN[d.month - 1]} {d.year}" if lang == "en" else f"{MONTHS_RU[d.month - 1]} {d.year}"


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def quarter_label(d: date, lang: str) -> str:
    q = (d.month - 1) // 3
    return f"Q{q + 1} {d.year}" if lang == "en" else f"{QUARTERS_RU[q]} кв. {d.year}"


def _fmt(d: date) -> str:
    return d.strftime("%d.%m.%Y")


class DateContext:
    def __init__(self, today: date) -> None:
        self.today = today

    # ---------------------------------------------------------------- relative periods

    def periods(self) -> list[Period]:
        t = self.today
        tomorrow = t + timedelta(days=1)
        week_start = t - timedelta(days=t.weekday())
        month = t.replace(day=1)
        last_month = add_months(month, -1)
        quarter = quarter_start(t)
        last_quarter = add_months(quarter, -3)
        year = date(t.year, 1, 1)
        return [
            Period("today", t, tomorrow, "сегодня", "today"),
            Period("yesterday", t - timedelta(days=1), t, "вчера", "yesterday"),
            Period("this_week", week_start, tomorrow, "эта неделя (с понедельника)", "this week (since Monday)"),
            Period("last_week", week_start - timedelta(days=7), week_start, "прошлая неделя", "last week"),
            Period("last_7_days", tomorrow - timedelta(days=7), tomorrow, "последние 7 дней", "last 7 days"),
            Period("last_30_days", tomorrow - timedelta(days=30), tomorrow, "последние 30 дней", "last 30 days"),
            Period("this_month", month, tomorrow, f"этот месяц ({month_label(month, 'ru')}, неполный)",
                   f"this month ({month_label(month, 'en')}, partial)"),
            Period("last_month", last_month, month, f"прошлый месяц ({month_label(last_month, 'ru')})",
                   f"last month ({month_label(last_month, 'en')})"),
            Period("this_quarter", quarter, tomorrow, f"этот квартал ({quarter_label(quarter, 'ru')}, неполный)",
                   f"this quarter ({quarter_label(quarter, 'en')}, partial)"),
            Period("last_quarter", last_quarter, quarter, f"прошлый квартал ({quarter_label(last_quarter, 'ru')})",
                   f"last quarter ({quarter_label(last_quarter, 'en')})"),
            Period("this_year", year, tomorrow, f"этот год ({t.year}, с 1 января)", f"this year ({t.year}, since Jan 1)"),
            Period("last_year", date(t.year - 1, 1, 1), year, f"прошлый год ({t.year - 1})", f"last year ({t.year - 1})"),
        ]

    def period(self, key: str) -> Period:
        for p in self.periods():
            if p.key == key:
                return p
        raise KeyError(key)

    def render_for_prompt(self, lang: str, data_range: tuple[date, date] | None = None) -> str:
        weekday_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        weekday_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        wd = weekday_en[self.today.weekday()] if lang == "en" else weekday_ru[self.today.weekday()]
        lines = [f"Today: {self.today.isoformat()} ({wd}). Today is the last day with data; tomorrow has not started.",
                 "Periods: first and last included day; in SQL use date >= start AND date < end (end = day after last)."]
        for p in self.periods():
            lines.append(f"- {p.key}: {p.label(lang)} → {p.start.isoformat()} … {p.last_day.isoformat()} "
                         f"(start={p.start.isoformat()}, end={p.end.isoformat()})")
        if data_range:
            lines.append(f"Available data: {data_range[0].isoformat()} … {data_range[1].isoformat()} (inclusive).")
        return "\n".join(lines)

    # ---------------------------------------------------------------- dashboard periods

    def dashboard_period(self, key: str, lang: str) -> tuple[Period, Period, bool]:
        """(period, comparable previous period, is_partial)."""
        t = self.today
        tomorrow = t + timedelta(days=1)
        if key == "month_to_date":
            start = t.replace(day=1)
            prev_start = add_months(start, -1)
            prev_days = calendar.monthrange(prev_start.year, prev_start.month)[1]
            elapsed = min(t.day, prev_days)
            current = Period(key, start, tomorrow, self._mtd_label(start, t, lang, "ru"), self._mtd_label(start, t, lang, "en"))
            prev_end = prev_start + timedelta(days=elapsed)
            previous = Period("previous", prev_start, prev_end, f"{month_label(prev_start, 'ru')}, 1–{elapsed}",
                              f"{month_label(prev_start, 'en')}, 1–{elapsed}")
            return current, previous, True
        if key == "last_month":
            start = add_months(t.replace(day=1), -1)
            end = t.replace(day=1)
            prev = add_months(start, -1)
            return (Period(key, start, end, month_label(start, "ru").capitalize(), month_label(start, "en")),
                    Period("previous", prev, start, month_label(prev, "ru").capitalize(), month_label(prev, "en")), False)
        if key == "quarter_to_date":
            start = quarter_start(t)
            prev = add_months(start, -3)
            elapsed = (tomorrow - start).days
            prev_end = min(start, prev + timedelta(days=elapsed))
            return (Period(key, start, tomorrow, f"{quarter_label(start, 'ru')} (по {_fmt(t)})", f"{quarter_label(start, 'en')} (to {_fmt(t)})"),
                    Period("previous", prev, prev_end, f"{quarter_label(prev, 'ru')}, те же дни", f"{quarter_label(prev, 'en')}, same days"), True)
        if key == "last_quarter":
            end = quarter_start(t)
            start = add_months(end, -3)
            prev = add_months(start, -3)
            return (Period(key, start, end, quarter_label(start, "ru"), quarter_label(start, "en")),
                    Period("previous", prev, start, quarter_label(prev, "ru"), quarter_label(prev, "en")), False)
        if key == "year_to_date":
            start = date(t.year, 1, 1)
            prev_start = date(t.year - 1, 1, 1)
            prev_end = prev_start + (tomorrow - start)
            return (Period(key, start, tomorrow, f"{t.year} (по {_fmt(t)})", f"{t.year} (to {_fmt(t)})"),
                    Period("previous", prev_start, prev_end, f"{t.year - 1}, тот же период", f"{t.year - 1}, same period"), True)
        if key == "last_30_days":
            start = tomorrow - timedelta(days=30)
            prev = start - timedelta(days=30)
            return (Period(key, start, tomorrow, "Последние 30 дней", "Last 30 days"),
                    Period("previous", prev, start, "Предыдущие 30 дней", "Previous 30 days"), True)
        raise KeyError(key)

    @staticmethod
    def _mtd_label(start: date, today: date, lang: str, want: str) -> str:
        if want == "en":
            return f"{month_label(start, 'en')} (to {_fmt(today)})"
        return f"{month_label(start, 'ru').capitalize()} (по {_fmt(today)})"

    @staticmethod
    def period_option_label(key: str, lang: str) -> str:
        labels = {
            "month_to_date": ("Этот месяц", "This month"),
            "last_month": ("Прошлый месяц", "Last month"),
            "quarter_to_date": ("Этот квартал", "This quarter"),
            "last_quarter": ("Прошлый квартал", "Last quarter"),
            "year_to_date": ("С начала года", "Year to date"),
            "last_30_days": ("Последние 30 дней", "Last 30 days"),
        }
        ru, en = labels[key]
        return en if lang == "en" else ru

    # ---------------------------------------------------------------- suggested question placeholders

    def placeholders(self, lang: str) -> dict[str, str]:
        month = self.today.replace(day=1)
        last = add_months(month, -1)
        return {
            "this_month": month_label(month, lang),
            "last_month": month_label(last, lang),
            "month_before_last": month_label(add_months(month, -2), lang),
            "this_year": str(self.today.year),
            "last_quarter": quarter_label(add_months(quarter_start(self.today), -3), lang),
        }
