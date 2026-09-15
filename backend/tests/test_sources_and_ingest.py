"""§56: data source registry, standard metric pack over canonical tables, multi-source API, ingest API."""

from __future__ import annotations

import gzip
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.connectors.base import ColumnInfo, SchemaInfo, TableInfo
from app.container import Container, DataSource, build_services
from app.ingest.contract import CanonicalContract
from app.ingest.service import IngestError, parse_tokens
from app.main import create_app
from app.semantic.sources import load_source_semantic, load_sources
from app.services.metrics import MetricService
from app.sql.executor import SafeSqlExecutor
from app.sql.validator import SqlValidator
from tests.conftest import (
    SEMANTIC_ROOT,
    FakeConnector,
    InMemoryStore,
    ScriptedProvider,
    llm_tools,
    result,
    submit,
)

CONTRACT = Path(os.environ.get("CONTRACTS_DIR") or Path(__file__).resolve().parents[2] / "contracts") / "canonical" / "v1.yaml"


@pytest.fixture(scope="module")
def registry():
    return load_sources(SEMANTIC_ROOT)


class CanonicalConnector(FakeConnector):
    id = "onec_demo"
    name = "1C"
    schema = "onec_demo"

    async def introspect(self) -> SchemaInfo:
        return SchemaInfo(schema="onec_demo", tables=[
            TableInfo(name="sales", schema="onec_demo", row_count=10,
                      columns=[ColumnInfo("id", "text"), ColumnInfo("date", "date"), ColumnInfo("amount", "numeric"),
                               ColumnInfo("_run_id", "uuid"), ColumnInfo("_loaded_at", "timestamptz")])])

    def public_details(self) -> dict[str, Any]:
        return {"database": "biznext", "schema": "onec_demo", "read_only_user": "agent_readonly"}


def canonical_responder(sql: str):
    if "min(" in sql.lower() and "max(" in sql.lower():
        return result([{"first_date": "2025-08-01", "last_date": "2026-09-14"}], {"first_date": "date", "last_date": "date"})
    if "AS dimension" in sql and "AS period" not in sql:
        return result([{"dimension": "Молоко", "value": 90.0}, {"dimension": "Сыры", "value": 60.0}])
    if "AS period" in sql:
        return result([{"period": "2026-07-01", "value": 100.0}, {"period": "2026-08-01", "value": 90.0}], {"period": "date"})
    if "inventory" in sql and "p.name AS product" in sql:
        return result([{"product": "Масло сливочное", "warehouse": "РЦ Астана", "quantity": 0.0, "reorder_point": 50.0}])
    if "SUM(inv.quantity)" in sql:
        return result([{"value": 3100.0}])
    if "SUM(s.quantity)" in sql:
        return result([{"value": 31000.0}])
    if "receivables" in sql or "HAVING max(s.date)" in sql:
        return result([])
    return result([{"value": 100.0}])


def test_registry_and_both_semantic_layers(registry) -> None:
    assert registry.default == "demo_postgres" and set(registry.sources) == {"demo_postgres", "onec_demo"}
    onec = registry.sources["onec_demo"]
    assert onec.storage == "canonical" and onec.schema_name == "onec_demo"
    layer = load_source_semantic(SEMANTIC_ROOT, onec)
    assert layer.data_dictionary.schema_name == "onec_demo"
    assert layer.business_context.company.name.ru == "Demo Foods"  # source override on top of the standard pack
    contract = CanonicalContract.load(CONTRACT)
    assert set(layer.data_dictionary.tables) == set(contract.entities)
    assert {"revenue", "cogs", "gross_profit", "gross_margin_pct", "production_volume", "inventory_units", "units_sold",
            "purchase_amount", "receivables_total", "inventory_turnover_days", "write_off_cost"} <= set(layer.metrics.metrics)


async def test_standard_metric_sql_stays_in_the_canonical_schema(registry) -> None:
    layer = load_source_semantic(SEMANTIC_ROOT, registry.sources["onec_demo"])
    validator = SqlValidator(allowed_schema="onec_demo", max_rows=500)
    metrics = MetricService(layer, SafeSqlExecutor(FakeConnector(), validator, max_rows=500, timeout_ms=1000))
    for key, metric in layer.metrics.metrics.items():
        if metric.kind not in ("simple", "snapshot"):
            continue
        for group_by in [None, *layer.metric_dimensions(key)]:
            sql = metrics.build_sql(key, date(2026, 8, 1), date(2026, 9, 1), group_by, "month" if group_by is None else None)
            checked = validator.validate(sql)
            assert checked.ok, (key, group_by, checked.message)
            assert not re.search(r"\bdemo\.", sql), sql
    for rule in layer.insights.rules:
        if getattr(rule, "sql", None):
            assert validator.validate(rule.sql).ok, rule.key
    # the canonical SQL must not pass for the direct demo source and vice versa
    assert not SqlValidator(allowed_schema="demo", max_rows=10).validate("SELECT count(*) FROM onec_demo.sales").ok


async def test_period_days_formula(registry) -> None:
    layer = load_source_semantic(SEMANTIC_ROOT, registry.sources["onec_demo"])
    connector = FakeConnector(canonical_responder)
    metrics = MetricService(layer, SafeSqlExecutor(connector, SqlValidator(allowed_schema="onec_demo", max_rows=500),
                                                   max_rows=500, timeout_ms=1000))
    computed = await metrics.compute("inventory_turnover_days", date(2026, 8, 1), date(2026, 9, 1))
    assert computed.values[(None, None)] == pytest.approx(3100 / 31000 * 31)


def two_source_client(settings: Settings, registry, provider: ScriptedProvider, ingest: Any = None) -> tuple[TestClient, InMemoryStore]:
    store = InMemoryStore()
    sources = {}
    for source_id, connector in (("demo_postgres", FakeConnector(canonical_responder)),
                                 ("onec_demo", CanonicalConnector(canonical_responder))):
        config = registry.sources[source_id]
        semantic = load_source_semantic(SEMANTIC_ROOT, config)
        services = build_services(settings, semantic, connector, store, provider, config, source_id)  # type: ignore[arg-type]
        sources[source_id] = DataSource(source_id, config, connector, services)  # type: ignore[arg-type]
    container = Container(settings, store, provider, sources, "demo_postgres", ingest)  # type: ignore[arg-type]
    return TestClient(create_app(container)), store


def test_api_routes_by_source(settings: Settings, registry) -> None:
    client, _ = two_source_client(settings, registry, ScriptedProvider([]))
    with client:
        meta = client.get("/api/meta?source=onec_demo").json()
        assert meta["source"]["id"] == "onec_demo" and meta["source"]["storage"] == "canonical"
        assert [s["id"] for s in meta["sources"]] == ["demo_postgres", "onec_demo"] and meta["default_source"] == "demo_postgres"
        assert client.get("/api/meta").json()["source"]["id"] == "demo_postgres"
        dashboard = client.get("/api/dashboard?source=onec_demo&period=last_month").json()
        assert [k["metric"] for k in dashboard["kpis"]][:2] == ["revenue", "operating_profit"]
        assert "costs_by_item" in {c["key"] for c in dashboard["charts"]}
        questions = [q["text"] for q in client.get("/api/suggested-questions?source=onec_demo").json()["questions"]]
        assert "Сколько продали сегодня?" in questions and "Какие товары дают 80% прибыли?" in questions
        assert client.get("/api/dashboard?source=nope").status_code == 404
        tables = client.get("/api/data-sources/onec_demo/tables").json()["tables"]
        assert [c["name"] for c in tables[0]["columns"]] == ["id", "date", "amount"]  # technical columns hidden
        listed = client.get("/api/data-sources").json()["sources"]
        assert [s["id"] for s in listed[:2]] == ["demo_postgres", "onec_demo"] and "1c" not in {s["id"] for s in listed}


def test_conversation_stays_in_its_source(settings: Settings, registry) -> None:
    steps = [
        llm_tools(("submit_answer", submit(summary="Итог 1C", answer="Ответ."))),
        llm_tools(("submit_answer", submit(summary="Итог follow-up", answer="Ответ."))),
    ]
    provider = ScriptedProvider(steps)
    client, store = two_source_client(settings, registry, provider)
    with client:
        first = client.post("/api/agent/ask", json={"question": "Сколько продали сегодня?", "source_id": "onec_demo"}).json()
        assert first["source_id"] == "onec_demo"
        follow = client.post("/api/agent/ask", json={"question": "А вчера?", "conversation_id": first["conversation_id"],
                                                     "source_id": "demo_postgres"}).json()
        assert follow["source_id"] == "onec_demo"
        listed = client.get("/api/conversations").json()["conversations"]
    assert listed[0]["source_id"] == "onec_demo"
    assert "Schema `onec_demo`" in provider.systems[0] and "canonical data contract" in provider.systems[1]


def test_canonical_contract_validation() -> None:
    contract = CanonicalContract.load(CONTRACT)
    good = {"id": "d:1", "document_id": "d", "date": "2026-09-14", "operation": "sale", "product_id": "p", "quantity": 2,
            "amount": 100.5, "vat_amount": None}
    assert contract.validate("sales", good) == []
    bad = {**good, "date": "14.09.2026", "quantity": "2", "operation": "gift", "salary": 1, "amount": True}
    problems = contract.validate("sales", bad)
    assert "unknown field 'salary'" in problems and "'date' must be a date YYYY-MM-DD" in problems
    assert "'quantity' must be a finite number" in problems and "'amount' must be a finite number" in problems
    assert "'operation' must be one of ['sale', 'return']" in problems
    assert contract.validate("products", {"id": "1"}) == ["'name' is required", "'item_type' is required"]
    assert parse_tokens("onec_demo:" + "x" * 30 + ", broken:short") == {"x" * 30: "onec_demo"}


class StubIngest:
    """IngestService stand-in: records calls; the database path is covered by integration tests."""

    def __init__(self) -> None:
        self.contract = CanonicalContract.load(CONTRACT)
        self.sources = {"onec_demo": type("S", (), {"entities": ["sales"]})()}
        self.enabled = True
        self.calls: list[tuple[str, Any]] = []

    def authenticate(self, token: str) -> str | None:
        return "onec_demo" if token == "t" * 32 else None

    async def state(self, source_id: str) -> dict[str, Any]:
        return {"source_id": source_id, "last_full_success_at": None}

    async def append_chunk(self, source_id: str, load_id: str, seq: int, records: list[Any]) -> int:
        self.calls.append(("chunk", (source_id, seq, records)))
        if records and records[0].get("bad"):
            raise IngestError(422, "contract_violation", "record 0: unknown field 'bad'")
        return len(records)

    async def close(self) -> None:
        return None


def test_ingest_api_auth_and_chunks(settings: Settings, registry) -> None:
    stub = StubIngest()
    client, _ = two_source_client(settings, registry, ScriptedProvider([]), ingest=stub)
    auth = {"Authorization": "Bearer " + "t" * 32}
    url = "/api/ingest/v1/loads/2f1c1b1e-8a1d-4a53-9f5a-111111111111/chunks"
    with client:
        assert client.get("/api/ingest/v1/state").status_code == 401
        assert client.get("/api/ingest/v1/state", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/api/ingest/v1/state", headers=auth).json()["source_id"] == "onec_demo"
        body = gzip.compress(json.dumps({"seq": 1, "records": [{"id": "1"}]}).encode())
        ok = client.post(url, content=body, headers={**auth, "Content-Encoding": "gzip", "Content-Type": "application/json"})
        assert ok.status_code == 200 and ok.json() == {"received": 1}
        rejected = client.post(url, json={"seq": 2, "records": [{"bad": 1}]}, headers=auth)
        assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "contract_violation"
        assert client.post(url, content=b"not gzip", headers={**auth, "Content-Encoding": "gzip"}).status_code == 400
        assert client.post(url, json={"records": []}, headers=auth).status_code == 400
    assert stub.calls[0][1][0] == "onec_demo"

    client_disabled, _ = two_source_client(settings, registry, ScriptedProvider([]))
    with client_disabled:
        assert client_disabled.get("/api/ingest/v1/state", headers=auth).status_code == 404
