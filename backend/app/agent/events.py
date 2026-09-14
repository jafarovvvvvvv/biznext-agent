"""Agent Trace steps: one vocabulary for SSE events, stored traces and the 'Show analysis' view (§7.7)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

StepEmitter = Callable[[dict[str, Any]], Awaitable[None]]

STEP_TITLES: dict[str, tuple[str, str]] = {
    "planning": ("Планирование", "Planning"),
    "schema_inspection": ("Изучение структуры данных", "Schema inspection"),
    "metric_calculation": ("Расчёт показателя", "Metric calculation"),
    "sql_generated": ("SQL сформирован", "SQL generated"),
    "sql_validation": ("Проверка SQL", "SQL validation"),
    "sql_execution": ("Выполнение SQL", "SQL execution"),
    "analysis": ("Анализ результатов", "Analysis"),
    "final_answer": ("Итоговый ответ", "Final answer"),
    "retry": ("Повторная попытка", "Retry"),
    "limit_reached": ("Достигнут лимит", "Limit reached"),
    "error": ("Ошибка", "Error"),
}


def step_title(step: str, lang: str) -> str:
    ru, en = STEP_TITLES.get(step, (step, step))
    return en if lang == "en" else ru


class TraceRecorder:
    def __init__(self, lang: str = "ru", emit: StepEmitter | None = None) -> None:
        self.lang = lang
        self._emit = emit
        self._steps: list[dict[str, Any]] = []
        self._started: dict[int, float] = {}

    @property
    def steps(self) -> list[dict[str, Any]]:
        return self._steps

    async def start(self, step: str, detail: dict[str, Any] | None = None) -> int:
        seq = len(self._steps) + 1
        record = {
            "seq": seq,
            "step": step,
            "status": "started",
            "title": step_title(step, self.lang),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "duration_ms": None,
            "detail": dict(detail or {}),
        }
        self._steps.append(record)
        self._started[seq] = time.perf_counter()
        await self._publish(record)
        return seq

    async def finish(self, seq: int, status: str = "completed", detail: dict[str, Any] | None = None) -> None:
        record = self._steps[seq - 1]
        record["status"] = status
        record["duration_ms"] = int((time.perf_counter() - self._started.pop(seq, time.perf_counter())) * 1000)
        if detail:
            record["detail"].update(detail)
        await self._publish(record)

    async def instant(self, step: str, status: str = "completed", detail: dict[str, Any] | None = None) -> int:
        seq = await self.start(step, detail)
        await self.finish(seq, status)
        return seq

    async def _publish(self, record: dict[str, Any]) -> None:
        if self._emit is not None:
            await self._emit({**record, "detail": dict(record["detail"])})
