import pytest

from app.sql.validator import SqlValidator


@pytest.fixture
def validator() -> SqlValidator:
    return SqlValidator(allowed_schema="demo", max_rows=500)


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "SELECT order_id FROM demo.sales_orders WHERE status <> 'cancelled'",
    "select count(*) from sales_orders",
    "WITH m AS (SELECT date_trunc('month', order_date) AS month, count(*) AS n FROM demo.sales_orders GROUP BY 1) SELECT * FROM m",
    "SELECT region_id FROM demo.regions UNION SELECT region_id FROM demo.warehouses",
    "SELECT sum(revenue_amount) FILTER (WHERE product_id = 1) FROM demo.sales_order_items;",
    "SELECT d::date FROM generate_series(DATE '2026-01-01', DATE '2026-01-31', interval '1 day') AS d",
    "SELECT name, rank() OVER (ORDER BY name) FROM demo.products",
])
def test_select_allowed(validator: SqlValidator, sql: str) -> None:
    result = validator.validate(sql)
    assert result.ok, result.message
    assert result.normalized_sql


@pytest.mark.parametrize(("sql", "code"), [
    ("INSERT INTO demo.regions VALUES (1, 'x', 'Юг')", "not_select"),
    ("UPDATE demo.products SET list_price = 0", "not_select"),
    ("DELETE FROM demo.sales_orders", "not_select"),
    ("DROP TABLE demo.sales_orders", "not_select"),
    ("ALTER TABLE demo.products ADD COLUMN x int", "not_select"),
    ("TRUNCATE demo.write_offs", "not_select"),
    ("CREATE TABLE demo.x (id int)", "not_select"),
    ("GRANT SELECT ON demo.products TO public", "not_select"),
    ("REVOKE SELECT ON demo.products FROM public", "not_select"),
    ("COPY demo.products TO '/tmp/x'", "not_select"),
    ("SET statement_timeout = 0", "not_select"),
    ("SELECT 1; DROP TABLE demo.sales_orders", "multiple_statements"),
    ("SELECT 1; SELECT 2", "multiple_statements"),
    ("WITH x AS (DELETE FROM demo.sales_orders RETURNING *) SELECT * FROM x", "forbidden_operation"),
    ("SELECT * INTO demo.copy FROM demo.products", "forbidden_operation"),
    ("SELECT * FROM demo.products FOR UPDATE", "forbidden_operation"),
    ("SELECT pg_sleep(10)", "forbidden_function"),
    ("SELECT pg_read_file('/etc/passwd')", "forbidden_function"),
    ("SELECT nextval('demo.seq')", "forbidden_function"),
    ("SELECT set_config('default_transaction_read_only', 'off', false)", "forbidden_function"),
    ("SELECT * FROM app.agent_traces", "forbidden_schema"),
    ("SELECT * FROM pg_catalog.pg_authid", "forbidden_schema"),
    ("SELECT * FROM information_schema.tables", "forbidden_schema"),
    ("SELECT * FROM pg_user", "forbidden_schema"),
    ("", "empty"),
    ("SELEC nothing FROM", "parse_error"),
])
def test_dangerous_rejected(validator: SqlValidator, sql: str, code: str) -> None:
    result = validator.validate(sql)
    assert not result.ok
    assert result.reason_code == code, (result.reason_code, result.message)


def test_comment_cannot_hide_second_statement(validator: SqlValidator) -> None:
    result = validator.validate("SELECT 1 /* harmless */; DELETE FROM demo.sales_orders -- tail")
    assert not result.ok


def test_limit_added_when_missing(validator: SqlValidator) -> None:
    result = validator.validate("SELECT * FROM demo.products")
    assert result.ok and result.normalized_sql.endswith("LIMIT 501")


def test_large_limit_capped(validator: SqlValidator) -> None:
    result = validator.validate("SELECT * FROM demo.products LIMIT 100000")
    assert result.ok and "LIMIT 501" in result.normalized_sql


def test_small_limit_kept(validator: SqlValidator) -> None:
    result = validator.validate("SELECT * FROM demo.products ORDER BY list_price DESC LIMIT 5")
    assert result.ok and result.normalized_sql.endswith("LIMIT 5")


def test_tables_reported(validator: SqlValidator) -> None:
    result = validator.validate("SELECT c.name FROM demo.customers c JOIN demo.sales_orders so ON so.customer_id = c.customer_id")
    assert result.tables == ["customers", "sales_orders"]


def test_too_long_rejected() -> None:
    validator = SqlValidator(allowed_schema="demo", max_rows=10, max_length=50)
    assert validator.validate("SELECT " + ", ".join(["1"] * 40)).reason_code == "too_long"
