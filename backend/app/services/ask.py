"""One user question end-to-end: conversation history → agent run → stored answer and trace."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any

from app.agent.agent import DataAgent, HistoryTurn
from app.llm.base import LLMProvider
from app.storage.app_db import AppStore

logger = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]

ERROR_MESSAGES = {
    "llm_not_configured": ("AI-сервис не настроен: укажите LLM_API_KEY в конфигурации.",
                           "AI service is not configured: set LLM_API_KEY."),
    "llm_unavailable": ("AI-сервис недоступен. Попробуйте позже.", "AI service unavailable. Please try again later."),
    "database_unavailable": ("Не удалось получить данные: база данных недоступна.",
                             "Could not retrieve data: the database is unavailable."),
    "internal_error": ("Внутренняя ошибка. Попробуйте ещё раз.", "Internal error. Please try again."),
}


def error_message(code: str, lang: str) -> str:
    ru, en = ERROR_MESSAGES.get(code, ERROR_MESSAGES["internal_error"])
    return en if lang == "en" else ru


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, trace_id: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.trace_id = trace_id


class AskService:
    def __init__(self, *, agent: DataAgent, store: AppStore, provider: LLMProvider, history_turns: int) -> None:
        self.agent = agent
        self.store = store
        self.provider = provider
        self.history_turns = history_turns

    async def ask(self, question: str, conversation_id: str | None, lang: str, today: date,
                  emit: EventEmitter | None = None) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ApiError(400, "invalid_request", "question is empty")
        if not self.provider.configured:
            raise ApiError(503, "llm_not_configured", error_message("llm_not_configured", lang))

        if conversation_id and not await self.store.conversation_exists(conversation_id):
            conversation_id = None
        if conversation_id is None:
            conversation_id = await self.store.create_conversation(question[:80])
        history = [self._history_turn(r) for r in
                   await self.store.recent_assistant_responses(conversation_id, self.history_turns)]
        await self.store.add_message(conversation_id, "user", question)

        trace_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc)
        if emit:
            await emit("start", {"trace_id": trace_id, "conversation_id": conversation_id, "message_id": message_id})

        async def emit_step(step: dict[str, Any]) -> None:
            if emit:
                await emit("step", step)

        logger.info("question received", extra={"trace_id": trace_id, "question": question[:500]})
        run = await self.agent.run(question, lang=lang, today=today, history=history, emit=emit_step)

        trace = {
            "trace_id": trace_id, "conversation_id": conversation_id, "message_id": message_id, "question": question,
            "status": run.status, "provider": self.provider.name, "model": self.provider.model,
            "created_at": created_at, "execution_ms": run.execution_ms, "llm_calls": run.llm_calls,
            "tool_calls": run.tool_calls, "sql_count": run.sql_count, "retry_count": run.retry_count,
            "input_tokens": run.usage.input_tokens, "output_tokens": run.usage.output_tokens, "steps": run.steps,
            "final_response": None, "grounding": run.grounding, "error": run.error_message,
        }

        if run.status == "error" or run.response is None:
            code = run.error_code or "internal_error"
            message = error_message(code, lang)
            failed = {"message_id": message_id, "conversation_id": conversation_id, "trace_id": trace_id,
                      "question": question, "status": "error", "summary": message, "answer": message,
                      "highlights": [], "metrics": [], "table": None, "chart": None,
                      "sources": {"tables": [], "metrics": []}, "sql": [], "limitations": None,
                      "follow_up_questions": [], "execution_ms": run.execution_ms,
                      "created_at": created_at.isoformat(timespec="seconds")}
            trace["final_response"] = failed
            await self._save(conversation_id, message_id, trace_id, message, failed, trace)
            raise ApiError(503 if code != "internal_error" else 500, code, message, trace_id)

        response = {"message_id": message_id, "conversation_id": conversation_id, "trace_id": trace_id,
                    "question": question, **run.response, "execution_ms": run.execution_ms,
                    "created_at": created_at.isoformat(timespec="seconds")}
        trace["final_response"] = response
        await self._save(conversation_id, message_id, trace_id, response["answer"], response, trace)
        return response

    async def _save(self, conversation_id: str, message_id: str, trace_id: str, content: str,
                    response: dict[str, Any], trace: dict[str, Any]) -> None:
        await self.store.add_message(conversation_id, "assistant", content or "", response=response,
                                     trace_id=trace_id, message_id=message_id)
        await self.store.save_trace(trace)

    @staticmethod
    def _history_turn(response: dict[str, Any]) -> HistoryTurn:
        return HistoryTurn(
            question=str(response.get("question") or ""),
            summary=str(response.get("summary") or ""),
            answer=str(response.get("answer") or ""),
            metrics=list((response.get("sources") or {}).get("metrics") or []),
            sql=[str(s.get("sql")) for s in (response.get("sql") or []) if s.get("sql")][-2:],
        )
