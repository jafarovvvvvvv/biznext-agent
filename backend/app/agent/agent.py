"""The data agent: one LLM + tools loop with hard limits (§7.6, §27)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.agent.date_context import DateContext
from app.agent.events import StepEmitter, TraceRecorder
from app.agent.grounding import unverified_numbers
from app.agent.prompts import build_system_prompt, reminder_to_submit
from app.agent.response_builder import build_response, text_only_response
from app.agent.results import ResultRegistry
from app.config import Settings
from app.connectors.base import DataConnector, DatabaseUnavailableError
from app.llm.base import LLMError, LLMMessage, LLMProvider, LLMUsage, ToolResult
from app.semantic.loader import SemanticLayer
from app.services.metrics import MetricService
from app.sql.executor import SafeSqlExecutor
from app.tools.base import AgentTool, ToolContext, error_content
from app.tools.registry import SUBMIT_ANSWER, default_tools, tool_specs

logger = logging.getLogger(__name__)


@dataclass
class HistoryTurn:
    question: str
    summary: str
    answer: str
    metrics: list[str] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)


@dataclass
class AgentRun:
    status: str  # answered | insufficient_data | clarification_needed | limit_reached | error
    response: dict[str, Any] | None
    steps: list[dict[str, Any]]
    usage: LLMUsage
    llm_calls: int = 0
    tool_calls: int = 0
    sql_count: int = 0
    retry_count: int = 0
    execution_ms: int = 0
    grounding: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


_LIMIT_TEXT = {
    "iterations": ("Не удалось завершить анализ в пределах лимита шагов агента. Попробуйте сузить или уточнить вопрос.",
                   "The analysis could not be completed within the agent step limit. Try a narrower question."),
    "tool_calls": ("Анализ потребовал слишком много обращений к данным и был остановлен. Попробуйте уточнить вопрос.",
                   "The analysis needed too many data requests and was stopped. Try a more specific question."),
    "sql_retries": ("Не удалось получить данные: запросы к базе несколько раз завершились ошибкой. Попробуйте переформулировать вопрос.",
                    "Could not retrieve the data: database queries failed repeatedly. Try rephrasing the question."),
    "timeout": ("Анализ занял слишком много времени и был остановлен. Попробуйте задать более узкий вопрос.",
                "The analysis took too long and was stopped. Try a narrower question."),
}


class DataAgent:
    def __init__(self, *, settings: Settings, semantic: SemanticLayer, connector: DataConnector,
                 executor: SafeSqlExecutor, metrics: MetricService, provider: LLMProvider,
                 tools: dict[str, AgentTool] | None = None) -> None:
        self.settings = settings
        self.semantic = semantic
        self.connector = connector
        self.executor = executor
        self.metrics = metrics
        self.provider = provider
        self.tools = tools or default_tools()
        self._system_prompts: dict[str, str] = {}

    def system_prompt(self, lang: str) -> str:
        if lang not in self._system_prompts:
            self._system_prompts[lang] = build_system_prompt(self.semantic, lang)
        return self._system_prompts[lang]

    async def run(self, question: str, *, lang: str = "ru", today: date, history: list[HistoryTurn] | None = None,
                  emit: StepEmitter | None = None) -> AgentRun:
        s = self.settings
        started = time.perf_counter()
        deadline = started + s.agent_timeout_seconds
        trace = TraceRecorder(lang, emit)
        results = ResultRegistry()
        usage = LLMUsage()
        ctx = ToolContext(settings=s, semantic=self.semantic, connector=self.connector, executor=self.executor,
                          metrics=self.metrics, results=results, trace=trace, lang=lang, today=today)
        counters = {"llm": 0, "tools": 0, "sql": 0, "retries": 0}

        def finish(status: str, response: dict[str, Any] | None, **extra: Any) -> AgentRun:
            return AgentRun(status=status, response=response, steps=trace.steps, usage=usage,
                            llm_calls=counters["llm"], tool_calls=counters["tools"], sql_count=counters["sql"],
                            retry_count=counters["retries"], execution_ms=int((time.perf_counter() - started) * 1000),
                            **extra)

        try:
            data_range = await self.metrics.data_range()
        except Exception:  # noqa: BLE001 - date context works without the data range
            data_range = None
        messages = [LLMMessage.user(self._user_prompt(question, lang, today, history or [], data_range))]
        specs = tool_specs(self.tools)
        system = self.system_prompt(lang)

        final_args: dict[str, Any] | None = None
        final_text: str | None = None
        limit: str | None = None
        reminded = False
        sql_failures_in_row = 0

        try:
            for iteration in range(s.agent_max_iterations):
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    limit = "timeout"
                    break
                seq = await trace.start("planning" if iteration == 0 else "analysis")
                try:
                    response = await asyncio.wait_for(
                        self.provider.complete(system=system, messages=messages, tools=specs), timeout=remaining)
                except asyncio.TimeoutError:
                    await trace.finish(seq, "failed", {"error": "timeout"})
                    limit = "timeout"
                    break
                except LLMError as exc:
                    await trace.finish(seq, "failed", {"error": exc.code})
                    await trace.instant("error", "failed", {"error": exc.message})
                    return finish("error", None, error_code=exc.code if exc.code != "llm_bad_request" else "llm_unavailable",
                                  error_message=exc.message)
                counters["llm"] += 1
                usage.add(response.usage)
                await trace.finish(seq, detail={"text": response.text[:2000]} if response.text else None)

                if response.stop_reason == "refusal":
                    await trace.instant("error", "failed", {"error": "model refused the request"})
                    return finish("error", None, error_code="llm_unavailable",
                                  error_message="AI service declined to process this request")

                messages.append(response.as_message())
                if not response.tool_calls:
                    if not reminded and iteration + 1 < s.agent_max_iterations:
                        reminded = True
                        messages.append(LLMMessage.user(reminder_to_submit(lang)))
                        continue
                    final_text = response.text
                    break

                submit = [c for c in response.tool_calls if c.name == SUBMIT_ANSWER]
                if submit and len(submit) == len(response.tool_calls):
                    final_args = submit[0].arguments
                    break

                tool_results: list[ToolResult] = []
                for call in response.tool_calls:
                    if call.name == SUBMIT_ANSWER:
                        tool_results.append(ToolResult(call.id, error_content(
                            "submit_too_early", "Call submit_answer alone, after reviewing the other tool results."), True))
                        continue
                    counters["tools"] += 1
                    if counters["tools"] > s.agent_max_tool_calls:
                        limit = "tool_calls"
                        break
                    tool = self.tools.get(call.name)
                    if tool is None:
                        tool_results.append(ToolResult(call.id, error_content("unknown_tool", f"tool '{call.name}' does not exist"), True))
                        continue
                    if "__invalid_arguments__" in call.arguments:
                        tool_results.append(ToolResult(call.id, error_content("invalid_arguments", "tool arguments are not valid JSON"), True))
                        continue
                    if call.name == "run_readonly_sql":
                        counters["sql"] += 1
                        if sql_failures_in_row:
                            counters["retries"] += 1
                            await trace.instant("retry", detail={"attempt": counters["retries"]})
                    outcome = await tool.run(ctx, call.arguments)
                    if outcome.sql_failed:
                        sql_failures_in_row += 1
                    elif outcome.sql_succeeded:
                        sql_failures_in_row = 0
                    tool_results.append(ToolResult(call.id, outcome.content, outcome.is_error))
                    if sql_failures_in_row >= s.agent_max_sql_retries:
                        limit = "sql_retries"
                        break
                if limit:
                    break
                messages.append(LLMMessage.results(tool_results))
            else:
                limit = "iterations"
        except DatabaseUnavailableError as exc:
            await trace.instant("error", "failed", {"error": "database unavailable"})
            return finish("error", None, error_code="database_unavailable", error_message=str(exc))

        symbol = self.semantic.business_context.currency.symbol
        grounding: dict[str, Any] = {}
        if final_args is not None:
            response_body, issues = build_response(final_args, results, lang, symbol)
            status = response_body["status"]
            if issues:
                grounding["reference_issues"] = list(issues)
        elif final_text is not None:
            status = "answered"
            response_body = text_only_response(status, final_text, results)
        else:
            status = "limit_reached"
            ru, en = _LIMIT_TEXT[limit or "iterations"]
            response_body = text_only_response(status, en if lang == "en" else ru, results,
                                               limitations=f"limit: {limit}")
            await trace.instant("limit_reached", "failed", {"limit": limit})

        texts = [response_body["summary"], response_body["answer"]] + [h["text"] for h in response_body["highlights"]]
        grounding["unverified"] = unverified_numbers(texts, results.numeric_values()) if status != "limit_reached" else []
        await trace.instant("final_answer", detail={"status": status, "results": len(results.all())})
        logger.info("agent run finished", extra={"status": status, "llm_calls": counters["llm"],
                                                 "tool_calls": counters["tools"], "sql": counters["sql"],
                                                 "retries": counters["retries"],
                                                 "unverified_numbers": len(grounding["unverified"])})
        return finish(status, response_body, grounding=grounding)

    def _user_prompt(self, question: str, lang: str, today: date, history: list[HistoryTurn],
                     data_range: tuple[date, date] | None) -> str:
        parts = ["# Date context", DateContext(today).render_for_prompt(lang, data_range)]
        turns = history[-self.settings.agent_history_turns:] if self.settings.agent_history_turns > 0 else []
        if turns:
            parts.append("\n# Earlier in this conversation (most recent last)")
            for number, turn in enumerate(turns, 1):
                parts.append(f"{number}. Question: {turn.question}")
                if turn.summary:
                    parts.append(f"   Summary: {turn.summary}")
                if turn.answer:
                    parts.append(f"   Answer: {turn.answer[:700]}")
                if turn.metrics:
                    parts.append(f"   Metrics used: {', '.join(turn.metrics)}")
                for sql in turn.sql[:2]:
                    parts.append(f"   SQL used: {sql[:600]}")
            parts.append("Resolve follow-up questions (e.g. 'and for July?', 'compare them') using this context.")
        parts.append("\n# Question")
        parts.append(question.strip())
        return "\n".join(parts)
