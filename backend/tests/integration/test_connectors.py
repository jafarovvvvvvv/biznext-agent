"""§56 integration: 1C demo source through the BizNext Connector (run inside the backend container).

    docker compose exec backend pytest -m integration tests/integration/test_connectors.py

Needs a completed connector sync (docker compose run --rm connector once --full).
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest

from app.agent.date_context import DateContext, today_in
from app.config import get_settings
from app.connectors.postgres import PostgresConnector
from app.ingest.contract import CanonicalContract
from app.ingest.service import IngestError, IngestService
from app.semantic.sources import load_source_semantic, load_sources
from app.services.metrics import MetricService
from app.sql.executor import SafeSqlExecutor
from app.sql.validator import SqlValidator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("INGEST_DATABASE_URL"), reason="INGEST_DATABASE_URL is not set"),
]

# metrics that must be identical whether the agent reads PostgreSQL directly or 1C through the connector
# inventory_below_reorder is compared on the current snapshot only: 1C keeps the current stock norm
# (InformationRegister_НормативыЗапасов), the demo database stores a reorder point per historical day.
RECONCILED = ["revenue", "cogs", "gross_profit", "operating_expenses", "write_off_cost", "operating_profit", "units_sold",
              "orders_count", "active_customers", "returns_amount", "production_volume", "purchase_amount",
              "inventory_units"]


@pytest.fixture
def settings():
    get_settings.cache_clear()
    return get_settings()


def metric_service(settings, source_id: str) -> MetricService:
    registry = load_sources(settings.semantic_dir)
    config = registry.sources[source_id]
    connector = PostgresConnector(settings.agent_database_url.get_secret_value(), schema=config.schema_name, source_id=source_id)
    executor = SafeSqlExecutor(connector, SqlValidator(allowed_schema=config.schema_name, max_rows=500), max_rows=500,
                               timeout_ms=settings.sql_statement_timeout_ms)
    return MetricService(load_source_semantic(settings.semantic_dir, config), executor)


def ingest_service(settings) -> IngestService:
    registry = load_sources(settings.semantic_dir)
    contract = CanonicalContract.load(Path(settings.contracts_dir) / "canonical" / "v1.yaml")
    return IngestService(settings.ingest_database_url.get_secret_value(), contract, registry.sources,
                         {"integration-test-token-0123456789": "onec_demo"})


async def test_connector_synced_all_entities(settings) -> None:
    async with await psycopg.AsyncConnection.connect(settings.agent_database_url.get_secret_value()) as conn:
        for table in ("products", "customers", "suppliers", "warehouses", "employees", "sales", "purchases", "production",
                      "payments", "costs", "write_offs", "inventory", "receivables"):
            count = (await (await conn.execute(f"SELECT count(*) FROM onec_demo.{table}")).fetchone())[0]
            assert count > 0, f"onec_demo.{table} is empty — run the connector first"
        latest = (await (await conn.execute("SELECT max(date) FROM onec_demo.sales")).fetchone())[0]
        assert latest >= today_in(settings.app_timezone).replace(day=1)


@pytest.mark.parametrize("period_key", ["last_month", "last_quarter"])
async def test_metrics_reconcile_between_direct_and_1c_sources(settings, period_key: str) -> None:
    direct, onec = metric_service(settings, "demo_postgres"), metric_service(settings, "onec_demo")
    period = DateContext(today_in(settings.app_timezone)).period(period_key)
    for key in RECONCILED:
        a = (await direct.compute(key, period.start, period.end)).values[(None, None)]
        b = (await onec.compute(key, period.start, period.end)).values[(None, None)]
        assert a is not None and b is not None, key
        assert abs(a - b) <= max(0.05, abs(a) * 1e-6), (key, a, b)
    by_category_a = (await direct.compute("revenue", period.start, period.end, group_by="category")).values
    by_category_b = (await onec.compute("revenue", period.start, period.end, group_by="category")).values
    assert by_category_a.keys() == by_category_b.keys()
    assert all(abs(by_category_a[k] - by_category_b[k]) < 0.05 for k in by_category_a)


async def test_current_low_stock_reconciles(settings) -> None:
    direct, onec = metric_service(settings, "demo_postgres"), metric_service(settings, "onec_demo")
    period = DateContext(today_in(settings.app_timezone)).period("last_7_days")
    a = (await direct.compute("inventory_below_reorder", period.start, period.end)).values[(None, None)]
    b = (await onec.compute("inventory_below_reorder", period.start, period.end)).values[(None, None)]
    assert a == b and a is not None


async def test_1c_only_metrics_are_plausible(settings) -> None:
    onec = metric_service(settings, "onec_demo")
    period = DateContext(today_in(settings.app_timezone)).period("last_month")
    for key in onec.semantic.metrics.metrics:
        computed = await onec.compute(key, period.start, period.end)
        assert computed.values.get((None, None)) is not None, key
    turnover = (await onec.compute("inventory_turnover_days", period.start, period.end)).values[(None, None)]
    assert 0 < turnover < 60
    receivables = (await onec.compute("receivables_total", period.start, period.end)).values[(None, None)]
    assert receivables > 0


@pytest.mark.parametrize("statement", [
    "SELECT count(*) FROM demo.sales_orders",
    "SELECT count(*) FROM app.conversations",
    "INSERT INTO demo.regions (region_id, name, macro_region) VALUES (99, 'x', 'Юг')",
    "CREATE TABLE onec_demo.hack (id int)",
    "DROP TABLE onec_demo.sales",
    "CREATE TEMP TABLE hack (id int)",
])
async def test_ingest_role_is_confined_to_canonical_schemas(settings, statement: str) -> None:
    async with await psycopg.AsyncConnection.connect(settings.ingest_database_url.get_secret_value(), autocommit=True) as conn:
        with pytest.raises(psycopg.Error) as excinfo:
            await conn.execute(statement)
        assert excinfo.value.sqlstate == "42501", excinfo.value.sqlstate


@pytest.mark.parametrize("statement", [
    "DELETE FROM onec_demo.sales",
    "UPDATE onec_demo.products SET name = 'x'",
    "SELECT count(*) FROM ingest.staging",
    "SELECT count(*) FROM ingest.runs",
])
async def test_agent_cannot_write_canonical_data_or_read_ingest(settings, statement: str) -> None:
    async with await psycopg.AsyncConnection.connect(settings.agent_database_url.get_secret_value(), autocommit=True) as conn:
        with pytest.raises(psycopg.Error) as excinfo:
            await conn.execute(statement)
        assert excinfo.value.sqlstate in ("25006", "42501"), excinfo.value.sqlstate


async def test_app_role_has_no_canonical_access(settings) -> None:
    async with await psycopg.AsyncConnection.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT count(*) FROM onec_demo.sales")


def test_backend_network_has_no_route_to_1c() -> None:
    """1C (the emulator) is only on the enterprise network; BizNext backend cannot even resolve it."""
    with pytest.raises(OSError):
        socket.create_connection(("onec-demo", 8080), timeout=3)


async def test_replace_window_is_atomic_and_idempotent(settings) -> None:
    """Loads into a far-past window that never has real data, then clears it with an empty load."""
    service = ingest_service(settings)
    window = (date(1990, 1, 1), date(1990, 1, 2))
    record = {"id": f"it:{uuid.uuid4()}", "document_id": "it", "date": "1990-01-01", "operation": "sale",
              "product_id": "it", "quantity": 1, "amount": 10.0}
    try:
        run_id = await service.start_run("onec_demo", "check", {"system": "integration-test"})
        load_id = await service.open_load("onec_demo", run_id, "sales", "replace_window", *window)
        with pytest.raises(IngestError) as outside:
            await service.append_chunk("onec_demo", load_id, 1, [{**record, "date": "1991-01-01"}])
        assert outside.value.status == 422
        with pytest.raises(IngestError):
            await service.append_chunk("onec_demo", load_id, 1, [{**record, "salary": 1}])
        assert await service.append_chunk("onec_demo", load_id, 1, [record]) == 1
        assert await service.append_chunk("onec_demo", load_id, 1, [record]) == 1  # retried chunk replaces itself
        with pytest.raises(IngestError) as mismatch:
            await service.commit("onec_demo", load_id, 2)
        assert mismatch.value.status == 409
        assert (await service.commit("onec_demo", load_id, 1))["inserted"] == 1
        with pytest.raises(IngestError):
            await service.commit("onec_demo", load_id, 1)  # already committed

        async def rows() -> int:
            async with await psycopg.AsyncConnection.connect(settings.agent_database_url.get_secret_value()) as conn:
                cur = await conn.execute("SELECT count(*) FROM onec_demo.sales WHERE date < '1991-01-01'")
                return (await cur.fetchone())[0]

        assert await rows() == 1
        empty = await service.open_load("onec_demo", run_id, "sales", "replace_window", *window)
        assert (await service.commit("onec_demo", empty, 0))["deleted"] == 1
        assert await rows() == 0
        with pytest.raises(IngestError):
            await service.open_load("onec_demo", run_id, "products", "replace_window", *window)  # wrong mode
        await service.finish_run("onec_demo", run_id, "succeeded", {"integration": True}, None)
    finally:
        async with await psycopg.AsyncConnection.connect(settings.ingest_database_url.get_secret_value(), autocommit=True) as conn:
            await conn.execute("DELETE FROM ingest.runs WHERE source_id = 'onec_demo' AND kind = 'check'")
        await service.close()
