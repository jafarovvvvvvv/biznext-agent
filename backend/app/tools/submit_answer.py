"""submit_answer: the structured final answer. Handled by the agent loop, never executed as data access."""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolOutcome

UNITS = ["currency", "number", "percent", "hours", "liters", "kg", "text", "date"]
_NULLABLE_STRING = {"type": ["string", "null"]}

SUBMIT_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "answer", "highlights", "metrics", "table", "chart", "limitations",
                 "follow_up_questions"],
    "properties": {
        "status": {"type": "string", "enum": ["answered", "insufficient_data", "clarification_needed"]},
        "summary": {"type": "string", "description": "One-sentence executive conclusion"},
        "answer": {"type": "string", "description": "Short answer for an executive, markdown allowed"},
        "highlights": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["sentiment", "text"],
                "properties": {"sentiment": {"type": "string", "enum": ["positive", "negative", "warning", "neutral"]},
                               "text": {"type": "string"}},
            },
        },
        "metrics": {
            "type": "array",
            "description": "KPI cards; values are taken from the referenced result cell",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["label", "result_id", "row", "value_column", "change_pct_column", "unit"],
                "properties": {
                    "label": {"type": "string"},
                    "result_id": {"type": "string"},
                    "row": {"type": "integer", "description": "0-based row index in the result"},
                    "value_column": {"type": "string"},
                    "change_pct_column": _NULLABLE_STRING,
                    "unit": {"type": "string", "enum": UNITS},
                },
            },
        },
        "table": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["result_id", "title", "columns", "column_labels"],
                    "properties": {
                        "result_id": {"type": "string"},
                        "title": {"type": "string"},
                        "columns": {"type": "array", "items": {"type": "string"},
                                    "description": "Columns to show in order; empty array = all"},
                        "column_labels": {
                            "type": "array",
                            "description": "Human-readable label and unit for each shown column",
                            "items": {"type": "object", "additionalProperties": False, "required": ["column", "label", "unit"],
                                      "properties": {"column": {"type": "string"}, "label": {"type": "string"},
                                                     "unit": {"type": "string", "enum": UNITS}}},
                        },
                    },
                },
            ]
        },
        "chart": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object", "additionalProperties": False,
                    "required": ["result_id", "type", "title", "x", "series"],
                    "properties": {
                        "result_id": {"type": "string"},
                        "type": {"type": "string", "enum": ["line", "bar", "area", "pie"]},
                        "title": {"type": "string"},
                        "x": {"type": "string", "description": "Column for the X axis / pie labels"},
                        "series": {
                            "type": "array",
                            "items": {"type": "object", "additionalProperties": False, "required": ["column", "label", "unit"],
                                      "properties": {"column": {"type": "string"}, "label": {"type": "string"},
                                                     "unit": {"type": "string", "enum": UNITS}}},
                        },
                    },
                },
            ]
        },
        "limitations": _NULLABLE_STRING,
        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
    },
}


class SubmitAnswerTool:
    name = "submit_answer"
    description = (
        "Submit the final structured answer. Call it exactly once, alone, after you have the data. Numbers in "
        "metrics/table/chart are referenced by result_id and taken from real query results. Use status "
        "'insufficient_data' when the data cannot reliably answer the question."
    )
    input_schema = SUBMIT_ANSWER_SCHEMA
    strict = True

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:  # pragma: no cover - agent handles
        return ToolOutcome("accepted")
