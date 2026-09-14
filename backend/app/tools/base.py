from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from app.agent.events import TraceRecorder
from app.agent.results import ResultRegistry
from app.config import Settings
from app.connectors.base import DataConnector
from app.semantic.loader import SemanticLayer
from app.services.metrics import MetricService
from app.sql.executor import SafeSqlExecutor


@dataclass
class ToolContext:
    settings: Settings
    semantic: SemanticLayer
    connector: DataConnector
    executor: SafeSqlExecutor
    metrics: MetricService
    results: ResultRegistry
    trace: TraceRecorder
    lang: str
    today: date


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    sql_failed: bool = False  # a SQL attempt failed validation or execution (counts toward SQL retries)
    sql_succeeded: bool = False


class AgentTool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    strict: bool

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome: ...


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def rows_for_llm(rows: list[dict[str, Any]], max_rows: int, max_chars: int) -> tuple[list[dict[str, Any]], bool]:
    """Compact slice of rows that fits the LLM data budget (§27: never send raw datasets to the model)."""
    selected: list[dict[str, Any]] = []
    size = 2
    for row in rows[:max_rows]:
        compact = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}
        encoded = len(to_json(compact)) + 1
        if size + encoded > max_chars:
            break
        selected.append(compact)
        size += encoded
    return selected, len(selected) < len(rows)


def error_content(code: str, message: str, hint: str | None = None) -> str:
    payload: dict[str, Any] = {"error": code, "message": message}
    if hint:
        payload["hint"] = hint
    return to_json(payload)
