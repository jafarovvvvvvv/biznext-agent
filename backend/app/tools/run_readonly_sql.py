"""run_readonly_sql tool."""

from __future__ import annotations

from typing import Any

from app.agent.results import ResultColumn
from app.tools.base import ToolContext, ToolOutcome, error_content, rows_for_llm, to_json


class RunReadonlySqlTool:
    name = "run_readonly_sql"
    description = (
        "Executes ONE read-only PostgreSQL SELECT (WITH/UNION allowed) against the business schema and returns rows. "
        "Aggregate in SQL (GROUP BY, SUM, COUNT, window functions) and compute differences, shares and percentage "
        "changes in SQL instead of returning raw transactions. Only tables of the business schema are accessible. "
        "Rows sent back are capped; the full result (up to the row limit) is kept for tables and charts via result_id."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single SELECT statement"},
            "purpose": {"type": "string", "description": "Short description of what this query answers"},
        },
        "required": ["sql", "purpose"],
        "additionalProperties": False,
    }
    strict = False

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        sql = str(arguments.get("sql") or "")
        purpose = str(arguments.get("purpose") or "")[:300]
        await ctx.trace.instant("sql_generated", detail={"tool": self.name, "sql": sql, "purpose": purpose})
        validation = ctx.executor.validator.validate(sql, max_rows=ctx.executor.max_rows)
        await ctx.trace.instant("sql_validation", "completed" if validation.ok else "failed",
                                {"validation": validation.public()})
        if not validation.ok:
            return ToolOutcome(
                error_content(validation.reason_code or "invalid_sql", validation.message or "invalid SQL",
                              "Only a single read-only SELECT over the business schema is allowed. Fix and retry."),
                is_error=True, sql_failed=True,
            )
        seq = await ctx.trace.start("sql_execution", {"tool": self.name})
        outcome = await ctx.executor.execute(sql, purpose=purpose)
        if outcome.error is not None or outcome.result is None:
            message = outcome.error.message if outcome.error else "execution failed"
            code = outcome.error.code if outcome.error else "sql_error"
            await ctx.trace.finish(seq, "failed", {"error": f"{code}: {message}"})
            return ToolOutcome(error_content(code, message, "Check column/table names in the schema and retry."),
                               is_error=True, sql_failed=True)
        result = outcome.result
        stored = ctx.results.add(
            kind="sql",
            purpose=purpose,
            columns=[ResultColumn(c.name, c.type) for c in result.columns],
            rows=result.rows,
            row_count=result.row_count,
            truncated=result.truncated,
            sql=[{"sql": outcome.executed_sql, "purpose": purpose, "row_count": result.row_count,
                  "execution_ms": result.execution_ms}],
            tables=list(outcome.validation.tables),
        )
        await ctx.trace.finish(seq, detail={"result_id": stored.result_id, "row_count": result.row_count,
                                            "execution_ms": result.execution_ms, "sql": outcome.executed_sql})
        llm_rows, truncated_for_llm = rows_for_llm(result.rows, ctx.settings.agent_max_rows_to_llm,
                                                   ctx.settings.agent_max_result_chars_to_llm)
        payload = {
            "result_id": stored.result_id,
            "columns": [{"name": c.name, "type": c.type} for c in result.columns],
            "row_count": result.row_count,
            "rows": llm_rows,
            "truncated_for_llm": truncated_for_llm,
            "truncated_by_limit": result.truncated,
            "execution_ms": result.execution_ms,
        }
        if truncated_for_llm:
            payload["note"] = "Only part of the rows is shown to you; aggregate further if you need all of them."
        return ToolOutcome(to_json(payload), sql_succeeded=True)
