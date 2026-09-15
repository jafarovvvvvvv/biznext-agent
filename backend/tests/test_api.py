from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.container import Container, build_services
from app.main import create_app
from app.semantic.loader import SemanticLayer
from tests.conftest import FakeConnector, InMemoryStore, ScriptedProvider, llm_tools, result, submit


def responder(sql: str):
    if "min(" in sql.lower() and "max(" in sql.lower():
        return result([{"first_date": "2025-08-01", "last_date": "2026-09-14"}], {"first_date": "date", "last_date": "date"})
    if "AS dimension" in sql and "AS period" not in sql:
        return result([{"dimension": "Молоко", "value": 90.0}, {"dimension": "Сыры", "value": 60.0}])
    if "AS period" in sql:
        return result([{"period": "2026-07-01", "value": 100.0}, {"period": "2026-08-01", "value": 90.0}], {"period": "date"})
    if "inventory_snapshots" in sql and "p.name AS product" in sql:
        return result([{"product": "Масло", "warehouse": "РЦ Астана", "quantity_on_hand": 5, "reorder_point": 50}])
    if "inventory_snapshots" in sql or "line_downtime" in sql or "paid_date IS NULL" in sql:
        return result([])
    return result([{"value": 100.0}])


def make_client(settings: Settings, semantic: SemanticLayer, provider: ScriptedProvider,
                connector: FakeConnector | None = None) -> tuple[TestClient, InMemoryStore]:
    connector = connector or FakeConnector(responder)
    store = InMemoryStore()
    services = build_services(settings, semantic, connector, store, provider)  # type: ignore[arg-type]
    container = Container.single(settings, connector, store, provider, services)  # type: ignore[arg-type]
    return TestClient(create_app(container)), store


def agent_script() -> list[Any]:
    return [
        llm_tools(("run_readonly_sql", {"sql": "SELECT 1 AS value FROM demo.sales_orders", "purpose": "x"}), text="План."),
        llm_tools(("submit_answer", submit(summary="Итог: 100", answer="Значение 100."))),
    ]


def test_health_and_meta(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([]))
    with client:
        assert client.get("/api/health").json() == {"status": "ok", "database": "ok", "llm": "configured"}
        meta = client.get("/api/meta").json()
        assert meta["company"]["currency"] == "KZT" and meta["data_range"] == {"start": "2025-08-01", "end": "2026-09-14"}
        assert meta["llm"]["configured"] is True and meta["features"]["admin_token_required"] is False


def test_health_degraded_without_database(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([], configured=False), FakeConnector(responder, ok=False))
    with client:
        assert client.get("/api/health").json() == {"status": "degraded", "database": "error", "llm": "not_configured"}


def test_ask_json_persists_history_and_trace(settings: Settings, semantic: SemanticLayer) -> None:
    client, store = make_client(settings, semantic, ScriptedProvider(agent_script()))
    with client:
        response = client.post("/api/agent/ask", json={"question": "Сколько продали?"})
        assert response.status_code == 200, response.text
        body = response.json()
        for key in ("message_id", "conversation_id", "trace_id", "status", "summary", "answer", "metrics", "table",
                    "chart", "sources", "sql", "execution_ms", "created_at", "highlights", "follow_up_questions"):
            assert key in body
        assert body["status"] == "answered" and body["sql"][0]["row_count"] == 1
        conversations = client.get("/api/conversations").json()["conversations"]
        assert conversations[0]["message_count"] == 2
        detail = client.get(f"/api/conversations/{body['conversation_id']}").json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        trace = client.get(f"/api/traces/{body['trace_id']}").json()
        assert trace["steps"] and trace["sql_count"] == 1 and "api_key" not in json.dumps(trace).lower()
        reports = client.get("/api/reports").json()["reports"]
        assert reports[0]["summary"] == "Итог: 100"


def test_ask_stream_events(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider(agent_script()))
    with client:
        with client.stream("POST", "/api/agent/ask/stream", json={"question": "Сколько продали?"}) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            payload = "".join(response.iter_text())
    events = [block for block in payload.split("\n\n") if block.startswith("event:")]
    names = [block.split("\n")[0].removeprefix("event: ") for block in events]
    assert names[0] == "start" and names[-1] == "final" and "step" in names
    final = json.loads(events[-1].split("\n", 1)[1].removeprefix("data: "))
    assert final["status"] == "answered"


def test_ask_without_llm_key(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([], configured=False))
    with client:
        response = client.post("/api/agent/ask", json={"question": "вопрос"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "llm_not_configured"


def test_ask_validation_error(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([]))
    with client:
        response = client.post("/api/agent/ask", json={"question": ""})
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


def test_dashboard_contract(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([]))
    with client:
        body = client.get("/api/dashboard?period=last_month").json()
    assert body["period"]["key"] == "last_month" and body["period"]["compare_start"]
    assert len(body["kpis"]) == len(semantic.dashboard.kpis)
    kpi = body["kpis"][0]
    for key in ("metric", "label", "unit", "value", "formatted", "change_pct", "direction", "sentiment", "sparkline"):
        assert key in kpi
    assert {c["key"] for c in body["charts"]} == {c.key for c in semantic.dashboard.charts}
    assert body["available_periods"][0]["label"]


def test_insights_deterministic_without_llm(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([], configured=False))
    with client:
        body = client.get("/api/insights").json()
    assert body["source"] == "deterministic"
    low_stock = [i for i in body["insights"] if i["id"] == "low_stock"]
    assert low_stock and low_stock[0]["severity"] == "warning" and "Масло" in low_stock[0]["text"]


def test_suggested_questions_filled(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([]))
    with client:
        questions = client.get("/api/suggested-questions").json()["questions"]
    assert questions and not any("{" in q["text"] for q in questions)


def test_data_sources(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([]))
    with client:
        sources = client.get("/api/data-sources").json()["sources"]
        tables = client.get("/api/data-sources/demo_postgres/tables").json()["tables"]
        assert client.get("/api/data-sources/unknown/tables").status_code == 404
    assert sources[0]["status"] == "connected" and sources[0]["details"]["read_only_user"] == "agent_readonly"
    assert {s["status"] for s in sources[1:]} == {"coming_soon"} and len(sources) == 6
    assert tables[0]["columns"]


@pytest.mark.parametrize(("token", "status"), [(None, 401), ("wrong", 401), ("secret-token", 200)])
def test_traces_require_admin_token(settings: Settings, semantic: SemanticLayer, token: str | None, status: int) -> None:
    settings.admin_token = SecretStr("secret-token")
    client, _ = make_client(settings, semantic, ScriptedProvider([]))
    with client:
        headers = {"X-Admin-Token": token} if token else {}
        assert client.get("/api/traces", headers=headers).status_code == status


def test_not_found_trace(settings: Settings, semantic: SemanticLayer) -> None:
    client, _ = make_client(settings, semantic, ScriptedProvider([]))
    with client:
        assert client.get("/api/traces/not-a-uuid").json()["error"]["code"] == "not_found"
