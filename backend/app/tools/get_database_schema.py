"""get_database_schema tool."""

from __future__ import annotations

from typing import Any

from app.connectors.base import DatabaseUnavailableError, SchemaInfo, SqlExecutionError, TableInfo
from app.tools.base import ToolContext, ToolOutcome, to_json


def _column_line(ctx: ToolContext, table: TableInfo, column_name: str, column_type: str, db_description: str | None) -> str:
    lang = ctx.lang
    doc = ctx.semantic.data_dictionary.tables.get(table.name)
    column_doc = doc.columns.get(column_name) if doc else None
    description = (column_doc.description.get(lang) if column_doc and column_doc.description else None) or db_description
    line = f"{column_name} {column_type}"
    if description:
        line += f" — {description}"
    if column_doc and column_doc.values:
        values = "; ".join(f"{k}={v.get(lang)}" for k, v in column_doc.values.items())
        line += f" [values: {values}]"
    return line


def describe_schema(ctx: ToolContext, schema: SchemaInfo) -> dict[str, Any]:
    lang = ctx.lang
    tables = []
    for table in schema.tables:
        doc = ctx.semantic.data_dictionary.tables.get(table.name)
        entry: dict[str, Any] = {
            "table": f"{schema.schema}.{table.name}",
            "description": (doc.description.get(lang) if doc and doc.description else None) or table.description,
        }
        if doc and doc.grain:
            entry["grain"] = doc.grain.get(lang)
        if doc and doc.business_meaning:
            entry["business_meaning"] = doc.business_meaning.get(lang)
        if table.row_count is not None:
            entry["approx_rows"] = table.row_count
        entry["columns"] = [_column_line(ctx, table, c.name, c.type, c.description) for c in table.columns]
        if table.foreign_keys:
            entry["foreign_keys"] = [f"{fk.column} -> {fk.ref_table}.{fk.ref_column}" for fk in table.foreign_keys]
        tables.append(entry)
    metrics = [
        {
            "key": key,
            "label": metric.label.get(lang),
            "unit": metric.unit,
            "kind": metric.kind,
            "description": metric.description.get(lang) if metric.description else None,
            "group_by": ctx.metrics.dimensions(key),
        }
        for key, metric in ctx.semantic.metrics.metrics.items()
    ]
    return {"schema": schema.schema, "tables": tables, "metrics": metrics}


class GetDatabaseSchemaTool:
    name = "get_database_schema"
    description = (
        "Returns all tables of the business database with descriptions, grain, columns (type, meaning, allowed values), "
        "foreign keys and approximate row counts, plus the catalog of predefined business metrics with the dimensions "
        "they can be grouped by. Call it before writing SQL unless the schema is already known in this conversation."
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
    strict = False

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        seq = await ctx.trace.start("schema_inspection", {"tool": self.name})
        try:
            schema = await ctx.connector.introspect()
        except (DatabaseUnavailableError, SqlExecutionError) as exc:
            await ctx.trace.finish(seq, "failed", {"error": str(exc)})
            raise
        payload = describe_schema(ctx, schema)
        await ctx.trace.finish(seq, detail={"tables": len(payload["tables"]), "metrics": len(payload["metrics"])})
        return ToolOutcome(to_json(payload))
