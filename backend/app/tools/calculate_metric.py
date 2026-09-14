"""calculate_metric tool."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.agent.results import ResultColumn
from app.services.metrics import GRANULARITIES, MetricError, MetricQueryError
from app.tools.base import ToolContext, ToolOutcome, error_content, rows_for_llm, to_json


class CalculateMetricTool:
    name = "calculate_metric"
    description = (
        "Deterministically calculates a predefined business metric (see metric catalog) for a period [start, end), "
        "optionally compared with another period, grouped by a dimension or split by time granularity. "
        "The system computes compare_value, change_abs and change_pct (relative %, or percentage points in change_abs "
        "for percent metrics). Prefer this tool over SQL for standard KPIs."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "description": "Metric key from the catalog, e.g. revenue"},
            "start": {"type": "string", "description": "Period start, ISO date, inclusive"},
            "end": {"type": "string", "description": "Period end, ISO date, exclusive"},
            "compare_start": {"type": "string", "description": "Comparison period start (optional)"},
            "compare_end": {"type": "string", "description": "Comparison period end, exclusive (optional)"},
            "group_by": {"type": "string", "description": "Dimension key (optional)"},
            "granularity": {"type": "string", "enum": list(GRANULARITIES), "description": "Time series granularity (optional)"},
            "top": {"type": "integer", "description": "Keep top N groups by value (optional)"},
        },
        "required": ["metric", "start", "end"],
        "additionalProperties": False,
    }
    strict = False

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        key = str(arguments.get("metric") or "")
        seq = await ctx.trace.start("metric_calculation", {"tool": self.name, "metric": key, "arguments": arguments})
        try:
            start = _date(arguments.get("start"), "start")
            end = _date(arguments.get("end"), "end")
            compare_start = _date(arguments.get("compare_start"), "compare_start", optional=True)
            compare_end = _date(arguments.get("compare_end"), "compare_end", optional=True)
            if (compare_start is None) != (compare_end is None):
                raise MetricError("compare_start and compare_end must be given together")
            group_by = arguments.get("group_by") or None
            granularity = arguments.get("granularity") or None
            top = arguments.get("top")
            top = int(top) if top else None
            metric = ctx.metrics.metric(key)
            comparison = await ctx.metrics.compare(key, start, end, compare_start, compare_end, group_by, granularity, top)
        except MetricError as exc:
            await ctx.trace.finish(seq, "failed", {"error": str(exc)})
            return ToolOutcome(error_content("invalid_metric_request", str(exc)), is_error=True)
        except MetricQueryError as exc:
            await ctx.trace.finish(seq, "failed", {"error": exc.message})
            return ToolOutcome(error_content("metric_query_failed", exc.message), is_error=True)

        with_compare = compare_start is not None
        rows = [r.as_dict(bool(group_by), bool(granularity), with_compare) for r in comparison.rows]
        columns: list[ResultColumn] = []
        if group_by:
            columns.append(ResultColumn("dimension", "text"))
        if granularity:
            columns.append(ResultColumn("period", "date"))
        columns.append(ResultColumn("value", "number", metric.unit))
        if with_compare:
            columns += [ResultColumn("compare_value", "number", metric.unit),
                        ResultColumn("change_abs", "number", metric.unit),
                        ResultColumn("change_pct", "number", "percent")]
        label = metric.label.get(ctx.lang)
        stored = ctx.results.add(
            kind="metric", purpose=label, columns=columns, rows=rows, row_count=len(rows), truncated=False,
            sql=[{"sql": s["sql"], "purpose": _purpose(ctx, str(s.get("metric") or key)), "row_count": s["row_count"],
                  "execution_ms": s["execution_ms"]} for s in comparison.sql],
            tables=comparison.tables, metric=key,
        )
        await ctx.trace.finish(seq, detail={"result_id": stored.result_id, "row_count": len(rows),
                                            "sql": [s["sql"] for s in comparison.sql]})
        llm_rows, truncated_for_llm = rows_for_llm(rows, ctx.settings.agent_max_rows_to_llm,
                                                   ctx.settings.agent_max_result_chars_to_llm)
        payload: dict[str, Any] = {
            "result_id": stored.result_id,
            "metric": key,
            "label": label,
            "unit": metric.unit,
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "rows": llm_rows,
            "row_count": len(rows),
            "truncated_for_llm": truncated_for_llm,
        }
        if with_compare:
            payload["compare_period"] = {"start": compare_start.isoformat(), "end": compare_end.isoformat()}  # type: ignore[union-attr]
        if group_by:
            payload["group_by"] = group_by
        if granularity:
            payload["granularity"] = granularity
        return ToolOutcome(to_json(payload))


def _purpose(ctx: ToolContext, key: str) -> str:
    metric = ctx.semantic.metrics.metrics.get(key)
    return f"{metric.label.get(ctx.lang)} ({key})" if metric else key


def _date(value: Any, name: str, optional: bool = False) -> date | None:
    if value in (None, ""):
        if optional:
            return None
        raise MetricError(f"'{name}' is required (ISO date)")
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise MetricError(f"'{name}' must be an ISO date YYYY-MM-DD") from exc
