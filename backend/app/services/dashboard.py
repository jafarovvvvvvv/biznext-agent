from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.agent.date_context import DateContext, Period, add_months
from app.semantic.loader import SemanticLayer
from app.services.formatting import direction, format_value, sentiment
from app.services.metrics import MetricService

_TRUNC_STEP = {"month": 1, "quarter": 3, "year": 12}


def series_start(end_exclusive: date, granularity: str, points: int) -> date:
    """Start of a series of `points` buckets whose last bucket contains end_exclusive - 1 day."""
    last_day = end_exclusive - timedelta(days=1)
    if granularity == "day":
        return last_day - timedelta(days=points - 1)
    if granularity == "week":
        monday = last_day - timedelta(days=last_day.weekday())
        return monday - timedelta(weeks=points - 1)
    step = _TRUNC_STEP[granularity]
    first = last_day.replace(day=1)
    if granularity == "quarter":
        first = date(first.year, 3 * ((first.month - 1) // 3) + 1, 1)
    elif granularity == "year":
        first = date(first.year, 1, 1)
    return add_months(first, -step * (points - 1))


def period_payload(period: Period, previous: Period, partial: bool, lang: str) -> dict[str, Any]:
    return {
        "key": period.key, "label": period.label(lang), "start": period.start.isoformat(), "end": period.end.isoformat(),
        "compare_label": previous.label(lang), "compare_start": previous.start.isoformat(),
        "compare_end": previous.end.isoformat(), "is_partial": partial,
    }


class DashboardService:
    def __init__(self, semantic: SemanticLayer, metrics: MetricService) -> None:
        self.semantic = semantic
        self.metrics = metrics

    def resolve_period(self, key: str | None, today: date, lang: str) -> tuple[Period, Period, bool]:
        config = self.semantic.dashboard
        key = key if key in config.periods else config.default_period
        return DateContext(today).dashboard_period(key, lang)

    async def build(self, period_key: str | None, today: date, lang: str) -> dict[str, Any]:
        config = self.semantic.dashboard
        period, previous, partial = self.resolve_period(period_key, today, lang)
        symbol = self.semantic.business_context.currency.symbol
        kpis = []
        for spec in config.kpis:
            metric = self.metrics.metric(spec.metric)
            comparison = await self.metrics.compare(spec.metric, period.start, period.end, previous.start, previous.end)
            row = comparison.rows[0] if comparison.rows else None
            value = row.value if row else None
            change = row.change_pct if row else None
            card: dict[str, Any] = {
                "metric": spec.metric, "label": metric.label.get(lang), "unit": metric.unit, "better": metric.better,
                "value": value, "formatted": format_value(value, metric.unit, lang, symbol),
                "compare_value": row.compare_value if row else None, "change_pct": change,
                "direction": direction(change), "sentiment": sentiment(change, metric.better), "sparkline": [],
            }
            if spec.sparkline:
                start = series_start(period.end, spec.sparkline.granularity, spec.sparkline.points)
                series = await self.metrics.compare(spec.metric, start, period.end, granularity=spec.sparkline.granularity)
                card["sparkline"] = [{"period": r.period, "value": r.value} for r in series.rows]
            kpis.append(card)

        charts = []
        for chart in config.charts:
            series_meta = [{"key": m, "label": self.metrics.metric(m).label.get(lang), "unit": self.metrics.metric(m).unit}
                           for m in chart.metrics]
            if chart.granularity:
                start = series_start(period.end, chart.granularity, chart.lookback or 12)
                by_period: dict[str, dict[str, Any]] = {}
                for m in chart.metrics:
                    comparison = await self.metrics.compare(m, start, period.end, granularity=chart.granularity)
                    for r in comparison.rows:
                        by_period.setdefault(r.period or "", {"period": r.period})[m] = r.value
                data = [by_period[k] for k in sorted(by_period)]
                charts.append({"key": chart.key, "type": chart.type, "title": chart.title.get(lang), "x_key": "period",
                               "x_type": "date", "series": series_meta, "data": data})
            else:
                by_dimension: dict[str, dict[str, Any]] = {}
                order: list[str] = []
                for m in chart.metrics:
                    comparison = await self.metrics.compare(m, period.start, period.end, group_by=chart.group_by,
                                                            top=chart.top)
                    for r in comparison.rows:
                        name = r.dimension or "—"
                        if name not in by_dimension:
                            by_dimension[name] = {"dimension": name}
                            order.append(name)
                        by_dimension[name][m] = r.value
                charts.append({"key": chart.key, "type": chart.type, "title": chart.title.get(lang), "x_key": "dimension",
                               "x_type": "category", "series": series_meta, "data": [by_dimension[n] for n in order]})

        data_range = await self.metrics.data_range()
        return {
            "period": period_payload(period, previous, partial, lang),
            "available_periods": [{"key": k, "label": DateContext.period_option_label(k, lang)} for k in config.periods],
            "kpis": kpis,
            "charts": charts,
            "data_range": {"start": data_range[0].isoformat(), "end": data_range[1].isoformat()} if data_range else None,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
