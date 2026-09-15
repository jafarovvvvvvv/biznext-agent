"""Deterministic metric calculation from the semantic layer (ARCHITECTURE.md §6.3).

SQL is composed only from YAML definitions and validated `date` values, then executed through the
same safe SQL layer as agent queries. Ratios, formulas and period-over-period changes are computed
in Python — never by the LLM.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.agent.date_context import add_months
from app.semantic.loader import SemanticLayer
from app.semantic.models import Metric, MetricSource, parse_formula
from app.services.formatting import change_pct
from app.sql.executor import SafeSqlExecutor

Key = tuple[str | None, str | None]  # (dimension value, period start ISO)
GRANULARITIES = ("day", "week", "month", "quarter", "year")


class MetricError(ValueError):
    """Invalid metric request (unknown metric/dimension, bad dates). Message is safe for the LLM/user."""


class MetricQueryError(RuntimeError):
    """A metric query failed validation or execution."""

    def __init__(self, metric: str, message: str) -> None:
        super().__init__(f"metric '{metric}': {message}")
        self.metric = metric
        self.message = message


@dataclass
class MetricComputation:
    metric: str
    values: dict[Key, float | None]
    sql: list[dict[str, object]] = field(default_factory=list)  # {sql, row_count, execution_ms, tables}
    tables: list[str] = field(default_factory=list)


@dataclass
class ComparisonRow:
    dimension: str | None
    period: str | None
    value: float | None
    compare_value: float | None = None
    change_abs: float | None = None
    change_pct: float | None = None

    def as_dict(self, with_dimension: bool, with_period: bool, with_compare: bool) -> dict[str, object]:
        row: dict[str, object] = {}
        if with_dimension:
            row["dimension"] = self.dimension
        if with_period:
            row["period"] = self.period
        row["value"] = self.value
        if with_compare:
            row["compare_value"] = self.compare_value
            row["change_abs"] = self.change_abs
            row["change_pct"] = self.change_pct
        return row


@dataclass
class MetricComparison:
    metric: str
    rows: list[ComparisonRow]
    sql: list[dict[str, object]]
    tables: list[str]


def _dedup(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _date_literal(value: date) -> str:
    if not isinstance(value, date):
        raise MetricError("dates must be ISO dates (YYYY-MM-DD)")
    return f"DATE '{value.isoformat()}'"


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


class MetricService:
    def __init__(self, semantic: SemanticLayer, executor: SafeSqlExecutor) -> None:
        self.semantic = semantic
        self.executor = executor

    # ------------------------------------------------------------------ catalog helpers

    def metric(self, key: str) -> Metric:
        if key not in self.semantic.metrics.metrics:
            raise MetricError(f"unknown metric '{key}'; available: {', '.join(self.semantic.metrics.metrics)}")
        return self.semantic.metrics.metrics[key]

    def dimensions(self, key: str) -> list[str]:
        return self.semantic.metric_dimensions(key)

    # ------------------------------------------------------------------ SQL composition

    def build_sql(self, key: str, start: date, end: date, group_by: str | None = None,
                  granularity: str | None = None) -> str:
        metric = self.metric(key)
        if metric.kind not in ("simple", "snapshot"):
            raise MetricError(f"metric '{key}' is derived ({metric.kind}) and has no direct SQL")
        source: MetricSource = self.semantic.metrics.sources[metric.source or ""]
        if granularity is not None and granularity not in GRANULARITIES:
            raise MetricError(f"granularity must be one of {', '.join(GRANULARITIES)}")
        if start >= end:
            raise MetricError("start must be earlier than end")

        select: list[str] = []
        group: list[str] = []
        joins: list[str] = []
        if group_by:
            dimension = source.dimensions.get(group_by)
            if dimension is None:
                raise MetricError(f"metric '{key}' cannot be grouped by '{group_by}'; "
                                  f"available: {', '.join(source.dimensions) or 'none'}")
            joins += dimension.joins
            select.append(f"{dimension.column} AS dimension")
            group.append(dimension.column)
        if granularity:
            period_expr = f"date_trunc('{granularity}', {source.date_column})::date"
            select.append(f"{period_expr} AS period")
            group.append(period_expr)
        select.append(f"{metric.expression} AS value")

        where = [f"{source.date_column} >= {_date_literal(start)}", f"{source.date_column} < {_date_literal(end)}"]
        where += [f"({condition})" for condition in source.filters]
        if metric.kind == "snapshot":
            inner_where = " AND ".join(where)
            if granularity:
                where.append(
                    f"{source.date_column} IN (SELECT max({source.date_column}) FROM {source.from_} WHERE {inner_where} "
                    f"GROUP BY date_trunc('{granularity}', {source.date_column}))"
                )
            else:
                where.append(f"{source.date_column} = (SELECT max({source.date_column}) FROM {source.from_} WHERE {inner_where})")

        sql = f"SELECT {', '.join(select)} FROM {source.from_}"
        if joins:
            sql += " " + " ".join(_dedup(joins))
        sql += " WHERE " + " AND ".join(where)
        if group:
            sql += " GROUP BY " + ", ".join(group)
            order = ["period"] if granularity else []
            order.append("value DESC NULLS LAST" if group_by else "")
            sql += " ORDER BY " + ", ".join(o for o in order if o)
        return sql

    # ------------------------------------------------------------------ computation

    async def compute(self, key: str, start: date, end: date, group_by: str | None = None,
                      granularity: str | None = None) -> MetricComputation:
        metric = self.metric(key)
        if group_by and group_by not in self.dimensions(key):
            raise MetricError(f"metric '{key}' cannot be grouped by '{group_by}'; "
                              f"available: {', '.join(self.dimensions(key)) or 'none'}")
        if metric.kind in ("simple", "snapshot"):
            return await self._compute_sql(key, start, end, group_by, granularity)
        components = self.semantic.component_metrics(key)
        results = {c: await self.compute(c, start, end, group_by, granularity) for c in components}
        keys = _dedup_keys([k for r in results.values() for k in r.values])
        sql = [s for r in results.values() for s in r.sql]
        tables = _dedup([t for r in results.values() for t in r.tables])
        values: dict[Key, float | None] = {}
        if metric.kind == "ratio":
            numerator, denominator = results[metric.numerator or ""], results[metric.denominator or ""]
            for k in keys:
                num = numerator.values.get(k)
                den = denominator.values.get(k)
                values[k] = None if num is None or not den else _round(num / den * 100)
        else:
            body = parse_formula(metric.formula or "")
            for k in keys:
                env = {c: (r.values.get(k) or 0.0) for c, r in results.items()}
                env["period_days"] = float(_period_days(k, start, end, granularity))
                values[k] = _round(_eval(body, env))
        return MetricComputation(key, values, sql, tables)

    async def _compute_sql(self, key: str, start: date, end: date, group_by: str | None,
                           granularity: str | None) -> MetricComputation:
        sql = self.build_sql(key, start, end, group_by, granularity)
        outcome = await self.executor.execute(sql, purpose=f"metric:{key}")
        if not outcome.ok or outcome.result is None:
            message = outcome.error.message if outcome.error else (outcome.validation.message or "validation failed")
            raise MetricQueryError(key, message)
        values: dict[Key, float | None] = {}
        for row in outcome.result.rows:
            dimension_value = row.get("dimension")
            k: Key = (
                (str(dimension_value) if dimension_value is not None else "—") if group_by else None,
                str(row["period"])[:10] if granularity else None,
            )
            raw = row.get("value")
            values[k] = None if raw is None else _round(float(raw))
        if not group_by and not granularity and not values:
            values[(None, None)] = None
        info = {"sql": outcome.executed_sql, "row_count": outcome.result.row_count,
                "execution_ms": outcome.result.execution_ms, "metric": key}
        return MetricComputation(key, values, [info], list(outcome.validation.tables))

    async def compare(self, key: str, start: date, end: date, compare_start: date | None = None,
                      compare_end: date | None = None, group_by: str | None = None,
                      granularity: str | None = None, top: int | None = None) -> MetricComparison:
        current = await self.compute(key, start, end, group_by, granularity)
        previous = None
        if compare_start and compare_end:
            if granularity:
                raise MetricError("compare_start/compare_end cannot be combined with granularity; request two series instead")
            previous = await self.compute(key, compare_start, compare_end, group_by, None)
        rows: list[ComparisonRow] = []
        keys = _dedup_keys(list(current.values) + (list(previous.values) if previous else []))
        for k in keys:
            value = current.values.get(k)
            row = ComparisonRow(dimension=k[0], period=k[1], value=value)
            if previous is not None:
                row.compare_value = previous.values.get(k)
                if value is not None and row.compare_value is not None:
                    row.change_abs = _round(value - row.compare_value)
                row.change_pct = change_pct(value, row.compare_value)
            rows.append(row)
        if granularity:
            rows.sort(key=lambda r: (r.period or "", r.dimension or ""))
        elif group_by:
            rows.sort(key=lambda r: (r.value is None, -(r.value or 0.0)))
        if top and group_by and not granularity:
            rows = rows[:top]
        sql = current.sql + (previous.sql if previous else [])
        tables = _dedup(current.tables + (previous.tables if previous else []))
        return MetricComparison(key, rows, sql, tables)

    async def data_range(self) -> tuple[date, date] | None:
        """Min/max date of the source behind the first dashboard KPI (falls back to the first metric)."""
        keys = [k.metric for k in self.semantic.dashboard.kpis] + list(self.semantic.metrics.metrics)
        for key in keys:
            metric = self.semantic.metrics.metrics.get(key)
            if metric and metric.kind == "simple":
                source = self.semantic.metrics.sources[metric.source or ""]
                sql = f"SELECT min({source.date_column})::date AS first_date, max({source.date_column})::date AS last_date FROM {source.from_}"
                outcome = await self.executor.execute(sql, purpose="data_range", max_rows=1)
                if outcome.ok and outcome.result and outcome.result.rows:
                    row = outcome.result.rows[0]
                    if row.get("first_date") and row.get("last_date"):
                        return date.fromisoformat(str(row["first_date"])[:10]), date.fromisoformat(str(row["last_date"])[:10])
                return None
        return None


def _period_days(key: Key, start: date, end: date, granularity: str | None) -> int:
    """Days in the requested period, or in the time bucket of a series point (clipped to the period)."""
    if not granularity or not key[1]:
        return (end - start).days
    bucket_start = date.fromisoformat(key[1])
    if granularity == "day":
        bucket_end = bucket_start + timedelta(days=1)
    elif granularity == "week":
        bucket_end = bucket_start + timedelta(days=7)
    else:
        bucket_end = add_months(bucket_start, {"month": 1, "quarter": 3, "year": 12}[granularity])
    return max((min(bucket_end, end) - max(bucket_start, start)).days, 0)


def _dedup_keys(keys: list[Key]) -> list[Key]:
    seen: dict[Key, None] = {}
    for k in keys:
        seen.setdefault(k, None)
    return list(seen)


def _eval(node: ast.expr, env: dict[str, float]) -> float | None:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return env.get(node.id, 0.0)
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, env)
        if operand is None:
            return None
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, env), _eval(node.right, env)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return None if right == 0 else left / right
    raise MetricError("unsupported formula element")
