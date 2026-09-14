"""Integration tests against the live PostgreSQL (run inside the backend container: pytest -m integration)."""

from __future__ import annotations

import os
from datetime import timedelta

import psycopg
import pytest

from app.agent.date_context import DateContext, today_in
from app.config import get_settings
from app.connectors.postgres import PostgresConnector
from app.semantic.loader import load_semantic
from app.services.dashboard import DashboardService
from app.services.insights import InsightsService
from app.services.metrics import MetricService
from app.sql.executor import SafeSqlExecutor
from app.sql.validator import SqlValidator
from app.storage.app_db import AppStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("AGENT_DATABASE_URL"), reason="AGENT_DATABASE_URL is not set"),
]

WRITE_ATTEMPTS = [
    "INSERT INTO demo.regions (region_id, name, macro_region) VALUES (99, 'x', 'Юг')",
    "UPDATE demo.products SET list_price = 1 WHERE product_id = 1",
    "DELETE FROM demo.sales_returns WHERE return_id = 1",
    "TRUNCATE demo.write_offs",
    "DROP TABLE demo.sales_plans",
    "CREATE TABLE demo.hack (id int)",
    "ALTER TABLE demo.products ADD COLUMN hack int",
    "GRANT SELECT ON demo.products TO PUBLIC",
    "CREATE TEMP TABLE hack_tmp (id int)",
    "SELECT * FROM app.agent_traces LIMIT 1",
]


@pytest.fixture
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def connector(settings):
    return PostgresConnector(settings.agent_database_url.get_secret_value(), schema=settings.data_schema)


@pytest.fixture
def metrics(settings, connector):
    semantic = load_semantic(settings.semantic_dir)
    executor = SafeSqlExecutor(connector, SqlValidator(allowed_schema=settings.data_schema, max_rows=500),
                               max_rows=500, timeout_ms=settings.sql_statement_timeout_ms)
    return MetricService(semantic, executor)


async def test_connection_and_introspection(connector) -> None:
    assert (await connector.test_connection()).ok
    schema = await connector.introspect()
    names = {t.name for t in schema.tables}
    assert {"sales_orders", "sales_order_items", "products", "inventory_snapshots"} <= names
    orders = schema.table("sales_orders")
    assert orders.row_count and orders.row_count > 1000
    assert any(fk.ref_table == "customers" for fk in orders.foreign_keys)
    assert orders.column("order_date").description


@pytest.mark.parametrize("statement", WRITE_ATTEMPTS)
async def test_agent_role_cannot_write_even_without_validator(settings, statement: str) -> None:
    """Database-level guarantee: raw connection as agent_readonly, validator bypassed."""
    async with await psycopg.AsyncConnection.connect(settings.agent_database_url.get_secret_value(), autocommit=True) as conn:
        with pytest.raises(psycopg.Error) as excinfo:
            await conn.execute(statement)
        assert excinfo.value.sqlstate in ("25006", "42501"), excinfo.value.sqlstate


async def test_read_write_transaction_override_still_denied(settings) -> None:
    async with await psycopg.AsyncConnection.connect(settings.agent_database_url.get_secret_value(), autocommit=True) as conn:
        await conn.execute("SET default_transaction_read_only = off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("UPDATE demo.products SET list_price = 1 WHERE product_id = 1")


async def test_app_user_has_no_business_data_access(settings) -> None:
    async with await psycopg.AsyncConnection.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT count(*) FROM demo.sales_orders")


async def test_every_metric_executes(metrics: MetricService) -> None:
    today = today_in(get_settings().app_timezone)
    period = DateContext(today).period("last_month")
    for key in metrics.semantic.metrics.metrics:
        computed = await metrics.compute(key, period.start, period.end)
        value = computed.values.get((None, None))
        assert value is not None, key
        dims = metrics.dimensions(key)
        if dims:
            grouped = await metrics.compute(key, period.start, period.end, group_by=dims[0])
            assert grouped.values, (key, dims[0])


async def test_revenue_is_plausible(metrics: MetricService) -> None:
    today = today_in(get_settings().app_timezone)
    period = DateContext(today).period("last_month")
    revenue = (await metrics.compute("revenue", period.start, period.end)).values[(None, None)]
    assert 100e6 < revenue < 2e9
    margin = (await metrics.compute("gross_margin_pct", period.start, period.end)).values[(None, None)]
    assert 5 < margin < 60


async def test_dashboard_and_insights_live(metrics: MetricService) -> None:
    today = today_in(get_settings().app_timezone)
    dashboard = DashboardService(metrics.semantic, metrics)
    body = await dashboard.build("last_month", today, "ru")
    assert all(k["value"] is not None for k in body["kpis"])
    assert all(c["data"] for c in body["charts"])
    insights = await InsightsService(metrics.semantic, metrics, metrics.executor, dashboard, None, 0).build("last_month", today, "ru")
    assert insights["insights"] and insights["source"] == "deterministic"


async def test_app_store_roundtrip(settings) -> None:
    store = AppStore(settings.database_url.get_secret_value())
    try:
        conversation_id = await store.create_conversation("integration test")
        await store.add_message(conversation_id, "user", "q")
        await store.add_message(conversation_id, "assistant", "a", response={"summary": "s", "question": "q"})
        found = await store.get_conversation(conversation_id)
        assert [m["role"] for m in found["messages"]] == ["user", "assistant"]
        assert (await store.recent_assistant_responses(conversation_id, 5))[0]["summary"] == "s"
        assert await store.delete_conversation(conversation_id)
    finally:
        await store.close()


def test_data_is_recent(settings) -> None:
    import asyncio

    async def latest():
        async with await psycopg.AsyncConnection.connect(settings.agent_database_url.get_secret_value()) as conn:
            cur = await conn.execute("SELECT max(order_date) FROM demo.sales_orders")
            return (await cur.fetchone())[0]

    last = asyncio.run(latest())
    assert last >= today_in(settings.app_timezone) - timedelta(days=45), "demo data is stale: run SEED_FORCE=1"
