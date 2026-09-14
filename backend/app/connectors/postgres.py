from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import errors as pg_errors
from psycopg import sql as pg_sql
from psycopg_pool import PoolTimeout

from app.connectors.base import (
    ColumnInfo,
    ConnectionStatus,
    DatabaseUnavailableError,
    ForeignKey,
    QueryColumn,
    QueryResult,
    SchemaInfo,
    SqlExecutionError,
    TableInfo,
)
from app.connectors.pg_pool import LazyPostgresPool, json_safe
from app.config import dsn_public_info

logger = logging.getLogger(__name__)

_TABLES_SQL = """
SELECT c.relname AS table_name,
       obj_description(c.oid, 'pg_class') AS description,
       GREATEST(c.reltuples, 0)::bigint AS row_estimate,
       c.reltuples < 0 AS never_analyzed
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       col_description(c.oid, a.attnum) AS description
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

_CONSTRAINTS_SQL = """
SELECT con.contype AS kind,
       cl.relname AS table_name,
       att.attname AS column_name,
       rcl.relname AS ref_table,
       ratt.attname AS ref_column
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class cl ON cl.oid = con.conrelid
JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
CROSS JOIN LATERAL unnest(con.conkey, COALESCE(con.confkey, con.conkey)) WITH ORDINALITY AS k(attnum, ref_attnum, ord)
JOIN pg_catalog.pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
LEFT JOIN pg_catalog.pg_class rcl ON rcl.oid = con.confrelid
LEFT JOIN pg_catalog.pg_attribute ratt ON ratt.attrelid = con.confrelid AND ratt.attnum = k.ref_attnum
WHERE con.contype IN ('p', 'f') AND ns.nspname = %(schema)s
ORDER BY cl.relname, con.conname, k.ord
"""

_SQLSTATE_CODES = {
    "57014": "timeout",
    "42501": "permission_denied",
    "25006": "read_only_violation",
    "42P01": "undefined_table",
    "42703": "undefined_column",
    "42883": "undefined_function",
    "42601": "syntax_error",
    "42803": "grouping_error",
    "42804": "datatype_mismatch",
    "22012": "division_by_zero",
}


def _map_db_error(exc: psycopg.Error) -> SqlExecutionError:
    sqlstate = getattr(exc, "sqlstate", None) or ""
    code = _SQLSTATE_CODES.get(sqlstate)
    if code is None:
        if sqlstate.startswith("42"):
            code = "sql_error"
        elif sqlstate.startswith("22"):
            code = "data_error"
        else:
            code = "database_error"
    diag = getattr(exc, "diag", None)
    fallback_text = str(exc).splitlines()[0] if str(exc) else "query failed"
    primary = getattr(diag, "message_primary", None) or fallback_text
    hint = getattr(diag, "message_hint", None)
    message = primary if not hint else f"{primary} (hint: {hint})"
    if code == "timeout":
        message = "query exceeded the statement timeout; aggregate more or add filters"
    return SqlExecutionError(code, message)


class PostgresConnector:
    """Read-only PostgreSQL data source (runs as the `agent_readonly` role)."""

    type = "postgresql"
    dialect = "postgres"

    def __init__(
        self,
        conninfo: str,
        *,
        schema: str,
        source_id: str = "postgres-demo",
        name: str = "PostgreSQL",
        introspection_ttl_s: float = 300.0,
        max_pool_size: int = 8,
    ) -> None:
        self.id = source_id
        self.name = name
        self.schema = schema
        self._public = dsn_public_info(conninfo)
        self._pool = LazyPostgresPool(conninfo, name=f"connector-{source_id}", max_size=max_pool_size)
        self._introspection_ttl_s = introspection_ttl_s
        self._schema_cache: tuple[float, SchemaInfo] | None = None

    def public_details(self) -> dict[str, Any]:
        return {"database": self._public["database"], "schema": self.schema, "read_only_user": self._public["user"]}

    async def close(self) -> None:
        await self._pool.close()

    async def test_connection(self) -> ConnectionStatus:
        started = time.perf_counter()
        checked_at = datetime.now(timezone.utc)
        try:
            await self._run_internal("SELECT 1", None, timeout_ms=3000)
        except (DatabaseUnavailableError, SqlExecutionError) as exc:
            return ConnectionStatus(ok=False, checked_at=checked_at, message=str(exc))
        return ConnectionStatus(ok=True, checked_at=checked_at, latency_ms=int((time.perf_counter() - started) * 1000))

    async def introspect(self) -> SchemaInfo:
        now = time.monotonic()
        if self._schema_cache and now - self._schema_cache[0] < self._introspection_ttl_s:
            return self._schema_cache[1]
        params = {"schema": self.schema}
        tables_rows = await self._run_internal(_TABLES_SQL, params, timeout_ms=10000)
        column_rows = await self._run_internal(_COLUMNS_SQL, params, timeout_ms=10000)
        constraint_rows = await self._run_internal(_CONSTRAINTS_SQL, params, timeout_ms=10000)

        tables: dict[str, TableInfo] = {}
        for row in tables_rows:
            tables[row["table_name"]] = TableInfo(
                name=row["table_name"],
                schema=self.schema,
                description=row["description"],
                row_count=None if row["never_analyzed"] else int(row["row_estimate"]),
            )
        for row in column_rows:
            table = tables.get(row["table_name"])
            if table is not None:
                table.columns.append(
                    ColumnInfo(
                        name=row["column_name"],
                        type=row["data_type"],
                        nullable=bool(row["nullable"]),
                        description=row["description"],
                    )
                )
        for row in constraint_rows:
            table = tables.get(row["table_name"])
            if table is None:
                continue
            if row["kind"] == "p":
                table.primary_key.append(row["column_name"])
            elif row["ref_table"]:
                table.foreign_keys.append(
                    ForeignKey(column=row["column_name"], ref_table=row["ref_table"], ref_column=row["ref_column"])
                )
        info = SchemaInfo(schema=self.schema, tables=list(tables.values()))
        self._schema_cache = (now, info)
        return info

    async def execute_readonly(
        self, sql: str, params: dict[str, Any] | None, max_rows: int, timeout_ms: int
    ) -> QueryResult:
        started = time.perf_counter()
        columns, rows = await self._execute(sql, params, timeout_ms=timeout_ms, fetch_limit=max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            execution_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _run_internal(self, sql: str, params: dict[str, Any] | None, timeout_ms: int) -> list[dict[str, Any]]:
        _, rows = await self._execute(sql, params, timeout_ms=timeout_ms, fetch_limit=None)
        return rows

    async def _execute(
        self, sql: str, params: dict[str, Any] | None, *, timeout_ms: int, fetch_limit: int | None
    ) -> tuple[list[QueryColumn], list[dict[str, Any]]]:
        pool = await self._pool.get()
        try:
            async with pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("SET TRANSACTION READ ONLY")
                    await conn.execute(
                        pg_sql.SQL("SET LOCAL statement_timeout = {}").format(pg_sql.Literal(int(timeout_ms)))
                    )
                    async with conn.cursor() as cur:
                        await cur.execute(sql, params if params else None)
                        if cur.description is None:
                            return [], []
                        columns = self._columns(conn, cur.description)
                        raw_rows = await (cur.fetchmany(fetch_limit) if fetch_limit is not None else cur.fetchall())
            names = [c.name for c in columns]
            rows = [{name: json_safe(value) for name, value in zip(names, raw, strict=False)} for raw in raw_rows]
            return columns, rows
        except PoolTimeout as exc:
            await self._pool.invalidate()
            raise DatabaseUnavailableError("database is unavailable (connection pool timeout)") from exc
        except psycopg.OperationalError as exc:
            if isinstance(exc, pg_errors.QueryCanceled):
                raise _map_db_error(exc) from exc
            raise DatabaseUnavailableError("database connection failed") from exc
        except psycopg.Error as exc:
            if getattr(exc, "sqlstate", None) is None:
                raise DatabaseUnavailableError("database connection failed") from exc
            raise _map_db_error(exc) from exc

    @staticmethod
    def _columns(conn: psycopg.AsyncConnection, description: Any) -> list[QueryColumn]:
        columns: list[QueryColumn] = []
        seen: dict[str, int] = {}
        for col in description:
            name = col.name or "column"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 1
            type_info = conn.adapters.types.get(col.type_code)
            columns.append(QueryColumn(name=name, type=type_info.name if type_info else "unknown"))
        return columns
