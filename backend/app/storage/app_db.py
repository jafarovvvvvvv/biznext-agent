"""Application storage (schema `app`): conversation history and agent traces.

Uses the application user (DATABASE_URL), never the agent role, and has no access to business data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout

from app.connectors.base import DatabaseUnavailableError
from app.connectors.pg_pool import LazyPostgresPool, json_safe

logger = logging.getLogger(__name__)

MIGRATION = """
CREATE TABLE IF NOT EXISTS app.conversations (
    id uuid PRIMARY KEY,
    title text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app.messages (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES app.conversations (id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    content text NOT NULL,
    response jsonb,
    trace_id uuid,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_conversation_idx ON app.messages (conversation_id, created_at);
CREATE TABLE IF NOT EXISTS app.agent_traces (
    trace_id uuid PRIMARY KEY,
    conversation_id uuid,
    message_id uuid,
    question text NOT NULL,
    status text NOT NULL,
    provider text,
    model text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    execution_ms integer,
    llm_calls integer NOT NULL DEFAULT 0,
    tool_calls integer NOT NULL DEFAULT 0,
    sql_count integer NOT NULL DEFAULT 0,
    retry_count integer NOT NULL DEFAULT 0,
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    steps jsonb NOT NULL DEFAULT '[]'::jsonb,
    final_response jsonb,
    grounding jsonb,
    error text
);
CREATE INDEX IF NOT EXISTS agent_traces_created_idx ON app.agent_traces (created_at DESC);
ALTER TABLE app.conversations ADD COLUMN IF NOT EXISTS source_id text;
ALTER TABLE app.agent_traces ADD COLUMN IF NOT EXISTS source_id text;
"""


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class AppStore:
    def __init__(self, conninfo: str) -> None:
        self._pool = LazyPostgresPool(conninfo, name="app-store", max_size=5)
        self._migrated = False
        self._migrate_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._pool.close()

    async def ping(self) -> bool:
        try:
            await self._fetch("SELECT 1 AS ok", {})
            return True
        except DatabaseUnavailableError:
            return False

    async def _ensure_schema(self) -> None:
        if self._migrated:
            return
        async with self._migrate_lock:
            if self._migrated:
                return
            pool = await self._pool.get()
            try:
                async with pool.connection() as conn:
                    await conn.execute(MIGRATION)
            except (psycopg.Error, PoolTimeout) as exc:
                await self._pool.invalidate()
                raise DatabaseUnavailableError("application storage is unavailable") from exc
            self._migrated = True
            logger.info("app schema ready")

    async def _fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        await self._ensure_schema()
        pool = await self._pool.get()
        try:
            async with pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(sql, params)
                    if cur.description is None:
                        return []
                    return [{k: json_safe(v) if not isinstance(v, (dict, list)) else v for k, v in row.items()}
                            for row in await cur.fetchall()]
        except (psycopg.OperationalError, PoolTimeout) as exc:
            await self._pool.invalidate()
            raise DatabaseUnavailableError("application storage is unavailable") from exc

    # ------------------------------------------------------------------ conversations

    async def create_conversation(self, title: str, source_id: str | None = None) -> str:
        conversation_id = str(uuid.uuid4())
        await self._fetch("INSERT INTO app.conversations (id, title, source_id) VALUES (%(id)s, %(title)s, %(source_id)s)",
                          {"id": conversation_id, "title": title[:120], "source_id": source_id})
        return conversation_id

    async def conversation_source(self, conversation_id: str) -> tuple[bool, str | None]:
        """(exists, source_id); conversations created before data sources existed have no source (default is used)."""
        rows = await self._fetch("SELECT source_id FROM app.conversations WHERE id = %(id)s", {"id": conversation_id})
        return (True, rows[0]["source_id"]) if rows else (False, None)

    async def conversation_exists(self, conversation_id: str) -> bool:
        rows = await self._fetch("SELECT 1 AS ok FROM app.conversations WHERE id = %(id)s", {"id": conversation_id})
        return bool(rows)

    async def add_message(self, conversation_id: str, role: str, content: str,
                          response: dict[str, Any] | None = None, trace_id: str | None = None,
                          message_id: str | None = None) -> str:
        message_id = message_id or str(uuid.uuid4())
        await self._fetch(
            "INSERT INTO app.messages (id, conversation_id, role, content, response, trace_id) "
            "VALUES (%(id)s, %(conversation_id)s, %(role)s, %(content)s, %(response)s, %(trace_id)s)",
            {"id": message_id, "conversation_id": conversation_id, "role": role, "content": content,
             "response": Jsonb(response) if response is not None else None, "trace_id": trace_id},
        )
        await self._fetch("UPDATE app.conversations SET updated_at = now() WHERE id = %(id)s", {"id": conversation_id})
        return message_id

    async def list_conversations(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT c.id, c.title, c.source_id, c.created_at, c.updated_at, count(m.id) AS message_count,
                   (SELECT m2.response ->> 'summary' FROM app.messages m2
                     WHERE m2.conversation_id = c.id AND m2.role = 'assistant'
                     ORDER BY m2.created_at DESC LIMIT 1) AS last_summary
            FROM app.conversations c LEFT JOIN app.messages m ON m.conversation_id = c.id
            GROUP BY c.id ORDER BY c.updated_at DESC LIMIT %(limit)s
            """,
            {"limit": limit},
        )
        return [{"id": str(r["id"]), "title": r["title"], "source_id": r["source_id"], "created_at": _iso(r["created_at"]),
                 "updated_at": _iso(r["updated_at"]), "message_count": int(r["message_count"]),
                 "last_summary": r["last_summary"]} for r in rows]

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        rows = await self._fetch("SELECT id, title, source_id, created_at FROM app.conversations WHERE id = %(id)s",
                                 {"id": conversation_id})
        if not rows:
            return None
        messages = await self._fetch(
            "SELECT id, role, content, response, created_at FROM app.messages "
            "WHERE conversation_id = %(id)s ORDER BY created_at, role DESC",
            {"id": conversation_id},
        )
        return {"id": str(rows[0]["id"]), "title": rows[0]["title"], "source_id": rows[0]["source_id"],
                "created_at": _iso(rows[0]["created_at"]),
                "messages": [{"id": str(m["id"]), "role": m["role"], "content": m["content"],
                              "response": m["response"], "created_at": _iso(m["created_at"])} for m in messages]}

    async def delete_conversation(self, conversation_id: str) -> bool:
        rows = await self._fetch("DELETE FROM app.conversations WHERE id = %(id)s RETURNING id", {"id": conversation_id})
        return bool(rows)

    async def recent_assistant_responses(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT response FROM app.messages
            WHERE conversation_id = %(id)s AND role = 'assistant' AND response IS NOT NULL
            ORDER BY created_at DESC LIMIT %(limit)s
            """,
            {"id": conversation_id, "limit": limit},
        )
        return [r["response"] for r in reversed(rows)]

    async def list_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT id, conversation_id, response, created_at FROM app.messages
            WHERE role = 'assistant' AND response IS NOT NULL
            ORDER BY created_at DESC LIMIT %(limit)s
            """,
            {"limit": limit},
        )
        reports = []
        for r in rows:
            response = r["response"] or {}
            reports.append({
                "message_id": str(r["id"]), "conversation_id": str(r["conversation_id"]),
                "source_id": response.get("source_id"),
                "question": response.get("question", ""), "summary": response.get("summary", ""),
                "status": response.get("status", ""), "has_chart": bool(response.get("chart")),
                "has_table": bool(response.get("table")), "created_at": _iso(r["created_at"]),
            })
        return reports

    # ------------------------------------------------------------------ traces

    async def save_trace(self, trace: dict[str, Any]) -> None:
        params = dict(trace)
        for key in ("steps", "final_response", "grounding"):
            params[key] = Jsonb(json.loads(json.dumps(params.get(key), default=str))) if params.get(key) is not None else None
        params.setdefault("finished_at", datetime.now(timezone.utc))
        params.setdefault("source_id", None)
        await self._fetch(
            """
            INSERT INTO app.agent_traces (trace_id, conversation_id, message_id, question, status, provider, model,
                created_at, finished_at, execution_ms, llm_calls, tool_calls, sql_count, retry_count, input_tokens,
                output_tokens, steps, final_response, grounding, error, source_id)
            VALUES (%(trace_id)s, %(conversation_id)s, %(message_id)s, %(question)s, %(status)s, %(provider)s, %(model)s,
                %(created_at)s, %(finished_at)s, %(execution_ms)s, %(llm_calls)s, %(tool_calls)s, %(sql_count)s,
                %(retry_count)s, %(input_tokens)s, %(output_tokens)s, %(steps)s, %(final_response)s, %(grounding)s,
                %(error)s, %(source_id)s)
            ON CONFLICT (trace_id) DO NOTHING
            """,
            params,
        )

    async def list_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT trace_id, question, status, source_id, created_at, execution_ms, llm_calls, tool_calls, sql_count,
                   retry_count
            FROM app.agent_traces ORDER BY created_at DESC LIMIT %(limit)s
            """,
            {"limit": limit},
        )
        return [_trace_summary(r) for r in rows]

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        rows = await self._fetch("SELECT * FROM app.agent_traces WHERE trace_id = %(id)s", {"id": trace_id})
        if not rows:
            return None
        r = rows[0]
        detail = _trace_summary(r)
        detail.update({
            "conversation_id": str(r["conversation_id"]) if r["conversation_id"] else None,
            "message_id": str(r["message_id"]) if r["message_id"] else None,
            "provider": r["provider"], "model": r["model"],
            "usage": {"input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"]},
            "steps": r["steps"] or [], "final_response": r["final_response"], "grounding": r["grounding"] or {},
            "error": r["error"],
        })
        return detail


def _trace_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {"trace_id": str(r["trace_id"]), "question": r["question"], "status": r["status"],
            "source_id": r.get("source_id"),
            "created_at": _iso(r["created_at"]), "execution_ms": r["execution_ms"], "llm_calls": r["llm_calls"],
            "tool_calls": r["tool_calls"], "sql_count": r["sql_count"], "retry_count": r["retry_count"]}
