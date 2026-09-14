"""get_table_metadata tool."""

from __future__ import annotations

import re
from typing import Any

from app.connectors.base import SchemaInfo
from app.tools.base import ToolContext, ToolOutcome, error_content, to_json
from app.tools.get_database_schema import describe_schema

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_TEXT_TYPES = ("text", "character varying", "character", "varchar")


class GetTableMetadataTool:
    name = "get_table_metadata"
    description = (
        "Detailed information about one table: columns with types and meaning, keys, approximate row count, "
        "3 sample rows and the distinct values of low-cardinality text columns."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"table": {"type": "string", "description": "Table name, e.g. 'sales_orders' or 'demo.sales_orders'"}},
        "required": ["table"],
        "additionalProperties": False,
    }
    strict = False

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        requested = str(arguments.get("table", ""))
        seq = await ctx.trace.start("schema_inspection", {"tool": self.name, "table": requested})
        schema = await ctx.connector.introspect()
        table = schema.table(requested)
        if table is None or not _IDENTIFIER.match(table.name):
            await ctx.trace.finish(seq, "failed", {"error": f"unknown table '{requested}'"})
            names = ", ".join(t.name for t in schema.tables)
            return ToolOutcome(error_content("unknown_table", f"table '{requested}' does not exist", f"available: {names}"),
                               is_error=True)
        qualified = f"{schema.schema}.{table.name}"
        info = describe_schema(ctx, SchemaInfo(schema=schema.schema, tables=[table]))["tables"][0]
        if table.primary_key:
            info["primary_key"] = table.primary_key

        sample = await ctx.executor.execute(f"SELECT * FROM {qualified} LIMIT 3", purpose=f"sample:{table.name}", max_rows=3)
        if sample.ok and sample.result:
            info["sample_rows"] = sample.result.rows

        distinct: dict[str, list[Any]] = {}
        text_columns = [c for c in table.columns if c.type.startswith(_TEXT_TYPES) and _IDENTIFIER.match(c.name)]
        for column in text_columns[:6]:
            outcome = await ctx.executor.execute(
                f'SELECT "{column.name}" AS value, count(*) AS n FROM {qualified} GROUP BY 1 ORDER BY 2 DESC LIMIT 21',
                purpose=f"distinct:{table.name}.{column.name}", max_rows=21,
            )
            if outcome.ok and outcome.result and outcome.result.row_count <= 20:
                distinct[column.name] = [r["value"] for r in outcome.result.rows]
        if distinct:
            info["distinct_values"] = distinct
        await ctx.trace.finish(seq, detail={"columns": len(table.columns)})
        return ToolOutcome(to_json(info))
