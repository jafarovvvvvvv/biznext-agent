from __future__ import annotations

import re
from typing import Any

import pytest

from app.agent.agent import DataAgent, HistoryTurn
from app.config import Settings
from app.connectors.base import DatabaseUnavailableError, SqlExecutionError
from app.llm.base import LLMError, LLMResponse, LLMUsage
from app.semantic.loader import SemanticLayer
from app.services.metrics import MetricService
from app.sql.executor import SafeSqlExecutor
from app.sql.validator import SqlValidator
from tests.conftest import TODAY, FakeConnector, ScriptedProvider, llm_text, llm_tools, result, submit

MONTHLY = [{"month": "2026-07-01", "revenue": 413_800_000.0}, {"month": "2026-08-01", "revenue": 403_900_000.0}]


def build_agent(settings: Settings, semantic: SemanticLayer, provider: ScriptedProvider,
                connector: FakeConnector | None = None) -> tuple[DataAgent, FakeConnector]:
    connector = connector or FakeConnector(lambda sql: result(MONTHLY, {"month": "date"}))
    executor = SafeSqlExecutor(connector, SqlValidator(allowed_schema="demo", max_rows=500), max_rows=500, timeout_ms=1000)
    agent = DataAgent(settings=settings, semantic=semantic, connector=connector, executor=executor,
                      metrics=MetricService(semantic, executor), provider=provider)
    return agent, connector


def events_collector() -> tuple[list[dict[str, Any]], Any]:
    events: list[dict[str, Any]] = []

    async def emit(step: dict[str, Any]) -> None:
        events.append(step)
    return events, emit


async def test_successful_answer_uses_real_result_values(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([
        llm_tools(("run_readonly_sql", {"sql": "SELECT month, revenue FROM demo.sales_orders", "purpose": "monthly revenue"}),
                  text="Посчитаю выручку по месяцам."),
        llm_tools(("submit_answer", submit(
            summary="Выручка за август — 403,9 млн ₸, это на 2,4% меньше июля.",
            answer="Выручка за август составила **403,9 млн ₸** против 413,8 млн ₸ в июле.",
            metrics=[{"label": "Выручка", "result_id": "r1", "row": 1, "value_column": "revenue",
                      "change_pct_column": None, "unit": "currency"}],
            table={"result_id": "r1", "title": "По месяцам", "columns": ["month", "revenue"],
                   "column_labels": [{"column": "revenue", "label": "Выручка", "unit": "currency"}]},
            chart={"result_id": "r1", "type": "bar", "title": "Выручка", "x": "month",
                   "series": [{"column": "revenue", "label": "Выручка", "unit": "currency"}]},
        ))),
    ])
    agent, _ = build_agent(settings, semantic, provider)
    events, emit = events_collector()
    run = await agent.run("Сколько мы продали в прошлом месяце?", today=TODAY, emit=emit)

    assert run.status == "answered"
    body = run.response
    assert body["metrics"][0]["value"] == 403_900_000.0
    assert body["metrics"][0]["formatted"] == "403,9 млн ₸"
    assert body["table"]["rows"] == MONTHLY and body["table"]["columns"][1] == {"key": "revenue", "label": "Выручка", "type": "currency"}
    assert body["chart"]["x_type"] == "date" and len(body["chart"]["data"]) == 2
    assert body["sql"][0]["sql"].startswith("SELECT") and body["sources"]["tables"] == ["sales_orders"]
    steps = [e["step"] for e in events if e["status"] != "started"]
    for expected in ("planning", "sql_generated", "sql_validation", "sql_execution", "analysis", "final_answer"):
        assert expected in steps
    assert run.llm_calls == 2 and run.sql_count == 1 and run.retry_count == 0
    # "2,4%" is not in the results (the model computed it) — reported, not blocking
    assert run.grounding["unverified"] == ["2,4%"]
    assert "Today: 2026-09-14" in provider.calls[0][0].text


async def test_sql_error_then_fix_counts_retry(settings: Settings, semantic: SemanticLayer) -> None:
    def respond(sql: str):
        if "revenu " in sql or "wrong_column" in sql:
            return SqlExecutionError("undefined_column", 'column "wrong_column" does not exist')
        return result(MONTHLY, {"month": "date"})

    provider = ScriptedProvider([
        llm_tools(("run_readonly_sql", {"sql": "SELECT wrong_column FROM demo.sales_orders", "purpose": "try"})),
        lambda messages: (_assert_error_passed(messages), llm_tools(
            ("run_readonly_sql", {"sql": "SELECT month, revenue FROM demo.sales_orders", "purpose": "fixed"})))[1],
        llm_tools(("submit_answer", submit(summary="Выручка 403,9 млн ₸", answer="403,9 млн ₸"))),
    ])
    agent, _ = build_agent(settings, semantic, provider, FakeConnector(respond))
    run = await agent.run("Выручка за август", today=TODAY)
    assert run.status == "answered" and run.retry_count == 1 and run.sql_count == 2
    assert any(s["step"] == "retry" for s in run.steps)


def _assert_error_passed(messages: list[Any]) -> None:
    last = messages[-1]
    assert last.role == "tool_results" and last.tool_results[0].is_error
    assert "wrong_column" in last.tool_results[0].content


async def test_validator_rejection_reaches_model(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([
        llm_tools(("run_readonly_sql", {"sql": "DELETE FROM demo.sales_orders", "purpose": "bad"})),
        llm_tools(("run_readonly_sql", {"sql": "DROP TABLE demo.sales_orders", "purpose": "bad again"})),
    ])
    agent, connector = build_agent(settings, semantic, provider)
    run = await agent.run("удали заказы", today=TODAY)
    assert run.status == "limit_reached"  # agent_max_sql_retries = 2
    assert not any("DELETE" in q or "DROP" in q for q in connector.queries)
    assert "Не удалось получить данные" in run.response["answer"]


async def test_iteration_limit(settings: Settings, semantic: SemanticLayer) -> None:
    settings.agent_max_iterations = 3
    provider = ScriptedProvider([llm_tools(("get_database_schema", {})) for _ in range(3)])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("бесконечный вопрос", today=TODAY)
    assert run.status == "limit_reached" and run.llm_calls == 3
    assert any(s["step"] == "limit_reached" for s in run.steps)


async def test_tool_budget_gives_one_chance_to_submit(settings: Settings, semantic: SemanticLayer) -> None:
    settings.agent_max_tool_calls = 2
    provider = ScriptedProvider([
        llm_tools(*[("get_database_schema", {})] * 3),
        lambda messages: (_check_budget_message(messages), llm_tools(("submit_answer", submit())))[1],
    ])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("много вызовов", today=TODAY)
    assert run.status == "answered" and run.tool_calls == 2
    assert any(s["step"] == "limit_reached" for s in run.steps)


def _check_budget_message(messages: list[Any]) -> None:
    results = messages[-1].tool_results
    assert [r.is_error for r in results] == [False, False, True] and "tool_budget_exhausted" in results[2].content


async def test_tool_call_limit_when_model_ignores_budget(settings: Settings, semantic: SemanticLayer) -> None:
    settings.agent_max_tool_calls = 2
    provider = ScriptedProvider([llm_tools(*[("get_database_schema", {})] * 3), llm_tools(("get_database_schema", {}))])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("много вызовов", today=TODAY)
    assert run.status == "limit_reached" and run.tool_calls == 2


async def test_insufficient_data_status(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([
        llm_tools(("get_database_schema", {})),
        llm_tools(("submit_answer", submit(status="insufficient_data", summary="В базе нет данных о конкурентах.",
                                           answer="Я не могу надёжно ответить: в подключённой базе нет информации о ценах конкурентов.",
                                           limitations="Нет данных о конкурентах"))),
    ])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("Какие цены у конкурентов?", today=TODAY)
    assert run.status == "insufficient_data" and run.response["limitations"]


async def test_text_answer_gets_one_reminder(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([llm_text("Думаю..."), llm_text("Окончательный текстовый ответ.")])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("вопрос", today=TODAY)
    assert run.status == "answered" and run.response["answer"] == "Окончательный текстовый ответ."
    assert "submit_answer" in provider.calls[1][-1].text


async def test_invalid_references_are_dropped(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([
        llm_tools(("run_readonly_sql", {"sql": "SELECT month, revenue FROM demo.sales_orders", "purpose": "x"})),
        llm_tools(("submit_answer", submit(
            metrics=[{"label": "fake", "result_id": "r9", "row": 0, "value_column": "revenue", "change_pct_column": None, "unit": "currency"}],
            chart={"result_id": "r1", "type": "line", "title": "t", "x": "missing", "series": []},
        ))),
    ])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("вопрос", today=TODAY)
    assert run.response["metrics"] == [] and run.response["chart"] is None
    assert run.grounding["reference_issues"]


async def test_submit_together_with_other_tools_is_deferred(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([
        llm_tools(("get_database_schema", {}), ("submit_answer", submit())),
        llm_tools(("submit_answer", submit())),
    ])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("вопрос", today=TODAY)
    assert run.status == "answered" and run.llm_calls == 2
    results = provider.calls[1][-1].tool_results
    assert any(r.is_error and "submit_too_early" in r.content for r in results)


async def test_calculate_metric_tool_result(settings: Settings, semantic: SemanticLayer) -> None:
    def respond(sql: str):
        current = re.search(r">=\s*(?:DATE\s*|CAST\()?'2026-08-01'", sql)  # sqlglot normalizes DATE literals
        return result([{"value": 403_900_000.0 if current else 413_800_000.0}])

    provider = ScriptedProvider([
        llm_tools(("calculate_metric", {"metric": "revenue", "start": "2026-08-01", "end": "2026-09-01",
                                        "compare_start": "2026-07-01", "compare_end": "2026-08-01"})),
        lambda messages: (_check_metric_payload(messages), llm_tools(("submit_answer", submit(
            metrics=[{"label": "Выручка", "result_id": "r1", "row": 0, "value_column": "value",
                      "change_pct_column": "change_pct", "unit": "currency"}]))))[1],
    ])
    agent, _ = build_agent(settings, semantic, provider, FakeConnector(respond))
    run = await agent.run("выручка август к июлю", today=TODAY)
    card = run.response["metrics"][0]
    assert card["value"] == 403_900_000.0 and card["change_pct"] == pytest.approx(-2.39, abs=0.01)
    assert card["direction"] == "down" and run.response["sources"]["metrics"] == ["revenue"]


def _check_metric_payload(messages: list[Any]) -> None:
    content = messages[-1].tool_results[0].content
    assert '"result_id":"r1"' in content and '"change_pct"' in content


async def test_llm_unavailable_returns_error(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([LLMError("llm_unavailable", "down")])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("вопрос", today=TODAY)
    assert run.status == "error" and run.error_code == "llm_unavailable"


async def test_database_unavailable_returns_error(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([llm_tools(("run_readonly_sql", {"sql": "SELECT 1", "purpose": "x"}))])
    agent, _ = build_agent(settings, semantic, provider,
                           FakeConnector(lambda sql: DatabaseUnavailableError("down")))
    run = await agent.run("вопрос", today=TODAY)
    assert run.status == "error" and run.error_code == "database_unavailable"


async def test_refusal_is_error(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([LLMResponse(text="", tool_calls=[], stop_reason="refusal", usage=LLMUsage(), model="m")])
    agent, _ = build_agent(settings, semantic, provider)
    run = await agent.run("вопрос", today=TODAY)
    assert run.status == "error"


async def test_history_is_passed_to_model(settings: Settings, semantic: SemanticLayer) -> None:
    provider = ScriptedProvider([llm_tools(("submit_answer", submit()))])
    agent, _ = build_agent(settings, semantic, provider)
    history = [HistoryTurn(question="Покажи продажи за август", summary="Выручка за август 403,9 млн ₸",
                           answer="…", metrics=["revenue"], sql=["SELECT ..."])]
    await agent.run("А за июль?", today=TODAY, history=history)
    prompt = provider.calls[0][0].text
    assert "Покажи продажи за август" in prompt and "Metrics used: revenue" in prompt and "А за июль?" in prompt


async def test_rows_sent_to_llm_are_capped(settings: Settings, semantic: SemanticLayer) -> None:
    settings.agent_max_rows_to_llm = 5
    many = [{"n": i} for i in range(300)]
    provider = ScriptedProvider([
        llm_tools(("run_readonly_sql", {"sql": "SELECT n FROM demo.sales_orders", "purpose": "many"})),
        lambda messages: (_check_capped(messages), llm_tools(("submit_answer", submit(
            table={"result_id": "r1", "title": "t", "columns": [], "column_labels": []}))))[1],
    ])
    agent, _ = build_agent(settings, semantic, provider, FakeConnector(lambda sql: result(many)))
    run = await agent.run("вопрос", today=TODAY)
    assert run.response["table"]["total_rows"] == 300 and len(run.response["table"]["rows"]) == 200


def _check_capped(messages: list[Any]) -> None:
    content = messages[-1].tool_results[0].content
    assert '"truncated_for_llm":true' in content and content.count('{"n":') == 5
