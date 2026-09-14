from datetime import date

import pytest

from app.agent.date_context import DateContext
from app.agent.grounding import extract_numbers, unverified_numbers
from app.semantic.loader import SemanticLayer
from app.services.dashboard import series_start
from app.services.formatting import change_pct, format_change_pct, format_value, sentiment
from app.services.metrics import MetricError, MetricService
from app.sql.executor import SafeSqlExecutor
from app.sql.validator import SqlValidator
from tests.conftest import FakeConnector, result


def service(semantic: SemanticLayer, connector: FakeConnector) -> MetricService:
    executor = SafeSqlExecutor(connector, SqlValidator(allowed_schema="demo", max_rows=500), max_rows=500, timeout_ms=1000)
    return MetricService(semantic, executor)


# ---------------------------------------------------------------- semantic layer


def test_real_semantic_layer_loads(semantic: SemanticLayer) -> None:
    assert "revenue" in semantic.metrics.metrics
    assert "region" in semantic.metric_dimensions("sales_plan_attainment_pct")
    assert semantic.dashboard.kpis


# ---------------------------------------------------------------- SQL composition


def test_simple_metric_sql_passes_validator(semantic: SemanticLayer) -> None:
    ms = service(semantic, FakeConnector())
    sql = ms.build_sql("revenue", date(2026, 8, 1), date(2026, 9, 1), group_by="category")
    assert "DATE '2026-08-01'" in sql and "so.status <> 'cancelled'" in sql and "pc.name AS dimension" in sql
    assert sql.count("JOIN demo.products p") == 1
    assert SqlValidator(allowed_schema="demo", max_rows=500).validate(sql).ok


@pytest.mark.parametrize("key", ["revenue", "orders_count", "production_volume", "downtime_hours", "avg_raw_milk_price",
                                 "inventory_below_reorder", "overdue_receivables", "sales_plan", "write_off_cost"])
def test_every_sql_metric_validates(semantic: SemanticLayer, key: str) -> None:
    ms = service(semantic, FakeConnector())
    validator = SqlValidator(allowed_schema="demo", max_rows=500)
    for group_by in [None, *semantic.metric_dimensions(key)[:2]]:
        for granularity in (None, "month"):
            sql = ms.build_sql(key, date(2026, 6, 1), date(2026, 9, 1), group_by, granularity)
            assert validator.validate(sql).ok, sql


def test_snapshot_uses_latest_date(semantic: SemanticLayer) -> None:
    sql = service(semantic, FakeConnector()).build_sql("inventory_below_reorder", date(2026, 8, 1), date(2026, 9, 1))
    assert "= (SELECT max(inv.snapshot_date)" in sql


def test_unknown_dimension_rejected(semantic: SemanticLayer) -> None:
    with pytest.raises(MetricError):
        service(semantic, FakeConnector()).build_sql("revenue", date(2026, 8, 1), date(2026, 9, 1), group_by="supplier")


def responder_by_expression(values: dict[str, float]):
    def respond(sql: str):
        for marker, value in values.items():
            if marker in sql:
                return result([{"value": value}])
        return result([{"value": 0.0}])
    return respond


async def test_ratio_and_formula_computed_in_python(semantic: SemanticLayer) -> None:
    connector = FakeConnector(responder_by_expression({
        "SUM(soi.revenue_amount) - SUM(soi.cost_amount)": 100.0,
        "SUM(soi.revenue_amount)": 400.0,
        "SUM(oe.amount)": 60.0,
        "SUM(wo.cost_amount)": 2.0,
    }))
    ms = service(semantic, connector)
    margin = await ms.compute("gross_margin_pct", date(2026, 8, 1), date(2026, 9, 1))
    assert margin.values[(None, None)] == 25.0
    profit = await ms.compute("operating_profit", date(2026, 8, 1), date(2026, 9, 1))
    assert profit.values[(None, None)] == 38.0
    assert len(profit.sql) == 3


async def test_compare_computes_change(semantic: SemanticLayer) -> None:
    def respond(sql: str):
        return result([{"value": 90.0 if "2026-08-01" in sql.split("<")[0] else 120.0}])
    ms = service(semantic, FakeConnector(respond))
    comparison = await ms.compare("revenue", date(2026, 8, 1), date(2026, 9, 1), date(2026, 7, 1), date(2026, 8, 1))
    row = comparison.rows[0]
    assert row.value == 90.0 and row.compare_value == 120.0
    assert row.change_abs == -30.0 and row.change_pct == -25.0


# ---------------------------------------------------------------- dates


def test_relative_periods() -> None:
    ctx = DateContext(date(2026, 9, 14))  # Monday
    assert ctx.period("yesterday").start == date(2026, 9, 13)
    assert ctx.period("this_week").start == date(2026, 9, 14)
    assert ctx.period("last_week").start == date(2026, 9, 7) and ctx.period("last_week").end == date(2026, 9, 14)
    assert ctx.period("last_month").start == date(2026, 8, 1) and ctx.period("last_month").end == date(2026, 9, 1)
    assert ctx.period("last_quarter").start == date(2026, 4, 1) and ctx.period("last_quarter").end == date(2026, 7, 1)
    assert ctx.period("this_year").start == date(2026, 1, 1)
    assert ctx.period("last_30_days").end == date(2026, 9, 15)


def test_year_boundary() -> None:
    ctx = DateContext(date(2026, 1, 1))
    assert ctx.period("last_month").start == date(2025, 12, 1)
    assert ctx.period("last_quarter").start == date(2025, 10, 1)
    assert ctx.period("last_year").start == date(2025, 1, 1)


def test_month_to_date_compares_same_days() -> None:
    current, previous, partial = DateContext(date(2026, 3, 31)).dashboard_period("month_to_date", "ru")
    assert partial and current.start == date(2026, 3, 1)
    assert previous.start == date(2026, 2, 1) and previous.end == date(2026, 3, 1)  # February has only 28 days


def test_placeholders() -> None:
    values = DateContext(date(2026, 9, 14)).placeholders("ru")
    assert values["last_month"] == "август 2026" and values["month_before_last"] == "июль 2026"


def test_series_start() -> None:
    assert series_start(date(2026, 9, 1), "month", 12) == date(2025, 9, 1)


# ---------------------------------------------------------------- formatting and grounding


def test_formatting() -> None:
    assert format_value(412_345_678, "currency", "ru") == "412,3 млн ₸"
    assert format_value(48_234_221, "currency", "ru") == "48,2 млн ₸"
    assert format_value(1_234_000_000, "currency", "en") == "₸1.23B"
    assert format_change_pct(-8.4, "ru") == "−8,4 %"
    assert change_pct(90, 120) == -25.0 and change_pct(5, 0) is None
    assert sentiment(10, "lower") == "negative" and sentiment(-10, "lower") == "positive"


def test_extract_numbers_scales() -> None:
    numbers = {round(v) for _, v, _ in extract_numbers("Выручка 48,2 млн ₸, рост на 8,4%, 1 234 заказа в 2026 году, 3 категории")}
    assert 48_200_000 in numbers and 8 in numbers and 1234 in numbers
    assert 2026 not in numbers and 3 not in numbers


def test_unverified_numbers() -> None:
    values = [48_234_221.0, -8.41, 1234.0]
    assert unverified_numbers(["Выручка 48,2 млн ₸, снижение на 8,4%, 1 234 заказа"], values) == []
    assert unverified_numbers(["Выручка 52,7 млн ₸"], values) == ["52,7 млн"]
