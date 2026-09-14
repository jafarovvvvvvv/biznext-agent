"""Builds the AgentResponse (§8.2) from submit_answer arguments. Values come only from stored results."""

from __future__ import annotations

from typing import Any

from app.agent.results import ResultRegistry, StoredResult
from app.services.formatting import direction, format_value

MAX_TABLE_ROWS = 200
MAX_CHART_POINTS = 120
MAX_PIE_SLICES = 12


class BuildIssues(list[str]):
    """Problems with references in submit_answer (dropped elements), recorded in the trace."""


def _column_type(result: StoredResult, column: str, unit: str | None) -> str:
    info = result.column(column)
    if unit in ("currency", "percent"):
        return unit
    if unit == "date" or (info is not None and info.is_date):
        return "date"
    if unit is None and info is not None and info.unit in ("currency", "percent"):
        return info.unit
    if unit in ("number", "hours", "liters", "kg") or (info is not None and info.is_numeric):
        return "number"
    return "text"


def build_metrics(items: list[dict[str, Any]], results: ResultRegistry, lang: str, symbol: str,
                  issues: BuildIssues) -> list[dict[str, Any]]:
    cards = []
    for item in items or []:
        result = results.get(str(item.get("result_id")))
        row_index = item.get("row")
        column = item.get("value_column")
        if result is None or not isinstance(row_index, int) or not (0 <= row_index < len(result.rows)):
            issues.append(f"metric '{item.get('label')}': unknown result or row")
            continue
        row = result.rows[row_index]
        value = row.get(column)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(f"metric '{item.get('label')}': column '{column}' is not numeric")
            continue
        change_column = item.get("change_pct_column")
        change = row.get(change_column) if change_column else None
        change = float(change) if isinstance(change, (int, float)) and not isinstance(change, bool) else None
        unit = item.get("unit") or (result.column(column).unit if result.column(column) else None) or "number"
        cards.append({
            "label": str(item.get("label") or column),
            "value": float(value),
            "unit": unit,
            "formatted": format_value(float(value), unit, lang, symbol),
            "change_pct": round(change, 2) if change is not None else None,
            "direction": direction(change),
        })
    return cards


METRIC_COLUMN_LABELS = {
    "dimension": ("Разрез", "Breakdown"), "period": ("Период", "Period"), "value": ("Значение", "Value"),
    "compare_value": ("База сравнения", "Comparison"), "change_abs": ("Изменение", "Change"),
    "change_pct": ("Изменение, %", "Change, %"),
}


def column_label(result: StoredResult, column: str, lang: str) -> str:
    if result.kind == "metric" and column in METRIC_COLUMN_LABELS:
        ru, en = METRIC_COLUMN_LABELS[column]
        return en if lang == "en" else ru
    return column


def build_table(spec: dict[str, Any] | None, results: ResultRegistry, issues: BuildIssues, lang: str = "ru") -> dict[str, Any] | None:
    if not spec:
        return None
    result = results.get(str(spec.get("result_id")))
    if result is None:
        issues.append(f"table: unknown result_id '{spec.get('result_id')}'")
        return None
    available = [c.name for c in result.columns]
    wanted = [c for c in (spec.get("columns") or []) if c in available] or available
    dropped = [c for c in (spec.get("columns") or []) if c not in available]
    if dropped:
        issues.append(f"table: unknown columns {dropped}")
    units = {u.get("column"): u.get("unit") for u in (spec.get("column_units") or []) if isinstance(u, dict)}
    columns = [{"key": c, "label": column_label(result, c, lang), "type": _column_type(result, c, units.get(c))}
               for c in wanted]
    rows = [{c: row.get(c) for c in wanted} for row in result.rows[:MAX_TABLE_ROWS]]
    return {
        "title": str(spec.get("title") or ""),
        "columns": columns,
        "rows": rows,
        "total_rows": result.row_count,
        "truncated": result.truncated or result.row_count > MAX_TABLE_ROWS,
    }


def build_chart(spec: dict[str, Any] | None, results: ResultRegistry, issues: BuildIssues) -> dict[str, Any] | None:
    if not spec:
        return None
    result = results.get(str(spec.get("result_id")))
    if result is None:
        issues.append(f"chart: unknown result_id '{spec.get('result_id')}'")
        return None
    x = spec.get("x")
    if result.column(str(x)) is None:
        issues.append(f"chart: unknown x column '{x}'")
        return None
    series = []
    for item in spec.get("series") or []:
        column = result.column(str(item.get("column")))
        if column is None:
            issues.append(f"chart: unknown series column '{item.get('column')}'")
            continue
        sample = next((r.get(column.name) for r in result.rows if r.get(column.name) is not None), None)
        if not isinstance(sample, (int, float)) or isinstance(sample, bool):
            issues.append(f"chart: series column '{column.name}' is not numeric")
            continue
        series.append({"key": column.name, "label": str(item.get("label") or column.name),
                       "unit": item.get("unit") or column.unit or "number"})
    if not series:
        issues.append("chart: no numeric series")
        return None
    chart_type = spec.get("type") if spec.get("type") in ("line", "bar", "area", "pie") else "bar"
    limit = MAX_PIE_SLICES if chart_type == "pie" else MAX_CHART_POINTS
    keys = [str(x)] + [s["key"] for s in series]
    data = [{k: row.get(k) for k in keys} for row in result.rows[:limit]]
    x_column = result.column(str(x))
    x_type = "date" if (x_column and x_column.is_date) or x == "period" else "category"
    return {"type": chart_type, "title": str(spec.get("title") or ""), "x_key": str(x), "x_type": x_type,
            "series": series, "data": data}


def build_sources(results: ResultRegistry) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    tables: list[str] = []
    metrics: list[str] = []
    sql: list[dict[str, Any]] = []
    for result in results.all():
        for table in result.tables:
            if table not in tables:
                tables.append(table)
        if result.metric and result.metric not in metrics:
            metrics.append(result.metric)
        for item in result.sql:
            sql.append({"result_id": result.result_id, "purpose": item.get("purpose") or result.purpose,
                        "sql": item.get("sql"), "row_count": item.get("row_count"),
                        "execution_ms": item.get("execution_ms")})
    return {"tables": tables, "metrics": metrics}, sql


def build_response(arguments: dict[str, Any], results: ResultRegistry, lang: str, currency_symbol: str) -> tuple[dict[str, Any], BuildIssues]:
    issues = BuildIssues()
    status = arguments.get("status")
    if status not in ("answered", "insufficient_data", "clarification_needed"):
        status = "answered"
    highlights = [
        {"sentiment": h.get("sentiment") if h.get("sentiment") in ("positive", "negative", "warning", "neutral") else "neutral",
         "text": str(h.get("text") or "")}
        for h in (arguments.get("highlights") or []) if isinstance(h, dict) and h.get("text")
    ]
    sources, sql = build_sources(results)
    limitations = arguments.get("limitations")
    response = {
        "status": status,
        "summary": str(arguments.get("summary") or ""),
        "answer": str(arguments.get("answer") or ""),
        "highlights": highlights,
        "metrics": build_metrics(arguments.get("metrics") or [], results, lang, currency_symbol, issues),
        "table": build_table(arguments.get("table"), results, issues, lang),
        "chart": build_chart(arguments.get("chart"), results, issues),
        "sources": sources,
        "sql": sql,
        "limitations": str(limitations) if limitations else None,
        "follow_up_questions": [str(q) for q in (arguments.get("follow_up_questions") or []) if q][:4],
    }
    return response, issues


def text_only_response(status: str, text: str, results: ResultRegistry, summary: str = "",
                       limitations: str | None = None) -> dict[str, Any]:
    sources, sql = build_sources(results)
    return {
        "status": status, "summary": summary, "answer": text, "highlights": [], "metrics": [], "table": None,
        "chart": None, "sources": sources, "sql": sql, "limitations": limitations, "follow_up_questions": [],
    }
