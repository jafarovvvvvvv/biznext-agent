from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.connectors.base import ColumnInfo, ConnectionStatus, ForeignKey, QueryColumn, QueryResult, SchemaInfo, TableInfo
from app.llm.base import LLMMessage, LLMResponse, LLMUsage, ToolCall, ToolSpec
from app.semantic.loader import SemanticLayer, load_semantic

REPO_SEMANTIC = Path(__file__).resolve().parents[2] / "semantic"
SEMANTIC_DIR = Path(os.environ.get("SEMANTIC_DIR") or REPO_SEMANTIC)
TODAY = date(2026, 9, 14)


@pytest.fixture(scope="session")
def semantic() -> SemanticLayer:
    return load_semantic(SEMANTIC_DIR)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql://app:x@localhost/db", agent_database_url="postgresql://agent:x@localhost/db",
        semantic_dir=str(SEMANTIC_DIR), llm_provider="anthropic", llm_model="claude-opus-5", llm_api_key="test-key",
        agent_max_iterations=6, agent_max_tool_calls=8, agent_max_sql_retries=2, agent_timeout_seconds=30,
    )


Responder = Callable[[str], QueryResult | Exception]


def result(rows: list[dict[str, Any]], types: dict[str, str] | None = None) -> QueryResult:
    names = list(rows[0].keys()) if rows else []
    types = types or {}
    columns = [QueryColumn(n, types.get(n, "numeric" if isinstance(rows[0][n], (int, float)) else "text")) for n in names]
    return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=False, execution_ms=3)


class FakeConnector:
    id = "postgres-demo"
    type = "postgresql"
    name = "PostgreSQL"
    dialect = "postgres"
    schema = "demo"

    def __init__(self, responder: Responder | None = None, ok: bool = True) -> None:
        self.responder = responder or (lambda sql: result([{"value": 1.0}]))
        self.ok = ok
        self.queries: list[str] = []

    async def test_connection(self) -> ConnectionStatus:
        return ConnectionStatus(ok=self.ok, checked_at=datetime.now(timezone.utc), latency_ms=1)

    async def introspect(self) -> SchemaInfo:
        return SchemaInfo(schema="demo", tables=[
            TableInfo(name="sales_orders", schema="demo", row_count=100, description="orders",
                      columns=[ColumnInfo("order_id", "bigint"), ColumnInfo("order_date", "date"),
                               ColumnInfo("status", "text"), ColumnInfo("customer_id", "integer")],
                      primary_key=["order_id"], foreign_keys=[ForeignKey("customer_id", "customers", "customer_id")]),
            TableInfo(name="customers", schema="demo", row_count=10,
                      columns=[ColumnInfo("customer_id", "integer"), ColumnInfo("name", "text")]),
        ])

    async def execute_readonly(self, sql: str, params: dict[str, Any] | None, max_rows: int, timeout_ms: int) -> QueryResult:
        self.queries.append(sql)
        outcome = self.responder(sql)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def public_details(self) -> dict[str, Any]:
        return {"database": "biznext", "schema": "demo", "read_only_user": "agent_readonly"}

    async def close(self) -> None:
        return None


class ScriptedProvider:
    """Returns prepared responses in order; each step is an LLMResponse or a callable(messages) -> LLMResponse."""

    name = "scripted"
    model = "scripted-model"

    def __init__(self, steps: list[Any], configured: bool = True) -> None:
        self.steps = list(steps)
        self._configured = configured
        self.calls: list[list[LLMMessage]] = []
        self.systems: list[str] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def complete(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int | None = None) -> LLMResponse:
        self.calls.append(list(messages))
        self.systems.append(system)
        if not self.steps:
            raise AssertionError("ScriptedProvider ran out of steps")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step


def llm_text(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], stop_reason="end_turn", usage=LLMUsage(10, 5), model="m")


def llm_tools(*calls: tuple[str, dict[str, Any]], text: str = "") -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[ToolCall(f"call_{uuid.uuid4().hex[:8]}", n, a) for n, a in calls],
                       stop_reason="tool_use", usage=LLMUsage(10, 5), model="m")


def submit(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": "answered", "summary": "Выручка за август 2026 — 403,9 млн ₸.", "answer": "Выручка составила 403,9 млн ₸.",
        "highlights": [], "metrics": [], "table": None, "chart": None, "limitations": None, "follow_up_questions": [],
    }
    body.update(overrides)
    return body


class InMemoryStore:
    def __init__(self) -> None:
        self.conversations: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = []
        self.traces: dict[str, dict[str, Any]] = {}

    async def close(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def create_conversation(self, title: str) -> str:
        cid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self.conversations[cid] = {"id": cid, "title": title, "created_at": now, "updated_at": now}
        return cid

    async def conversation_exists(self, conversation_id: str) -> bool:
        return conversation_id in self.conversations

    async def add_message(self, conversation_id: str, role: str, content: str, response: dict[str, Any] | None = None,
                          trace_id: str | None = None, message_id: str | None = None) -> str:
        mid = message_id or str(uuid.uuid4())
        self.messages.append({"id": mid, "conversation_id": conversation_id, "role": role, "content": content,
                              "response": response, "trace_id": trace_id,
                              "created_at": datetime.now(timezone.utc).isoformat()})
        return mid

    async def list_conversations(self, limit: int = 100) -> list[dict[str, Any]]:
        return [{**c, "message_count": sum(1 for m in self.messages if m["conversation_id"] == c["id"]),
                 "last_summary": None} for c in self.conversations.values()]

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        c = self.conversations.get(conversation_id)
        if not c:
            return None
        return {**c, "messages": [m for m in self.messages if m["conversation_id"] == conversation_id]}

    async def delete_conversation(self, conversation_id: str) -> bool:
        return self.conversations.pop(conversation_id, None) is not None

    async def recent_assistant_responses(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        items = [m["response"] for m in self.messages
                 if m["conversation_id"] == conversation_id and m["role"] == "assistant" and m["response"]]
        return items[-limit:]

    async def list_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        return [{"message_id": m["id"], "conversation_id": m["conversation_id"], "question": m["response"].get("question"),
                 "summary": m["response"].get("summary"), "status": m["response"].get("status"),
                 "has_chart": bool(m["response"].get("chart")), "has_table": bool(m["response"].get("table")),
                 "created_at": m["created_at"]} for m in self.messages if m["role"] == "assistant" and m["response"]]

    async def save_trace(self, trace: dict[str, Any]) -> None:
        self.traces[trace["trace_id"]] = trace

    async def list_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        return [{k: t.get(k) for k in ("trace_id", "question", "status", "execution_ms", "llm_calls", "tool_calls",
                                       "sql_count", "retry_count")} for t in self.traces.values()]

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        t = self.traces.get(trace_id)
        if t is None:
            return None
        return {**t, "usage": {"input_tokens": t["input_tokens"], "output_tokens": t["output_tokens"]},
                "created_at": str(t["created_at"])}
