from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


class DatabaseUnavailableError(RuntimeError):
    """The data source cannot be reached (network, auth, pool exhausted)."""


class SqlExecutionError(RuntimeError):
    """The database rejected or failed a query. Message is safe to show (no stack trace, no secrets)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ConnectionStatus:
    ok: bool
    checked_at: datetime
    latency_ms: int | None = None
    message: str | None = None


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True
    description: str | None = None


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class TableInfo:
    name: str
    schema: str
    columns: list[ColumnInfo] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    row_count: int | None = None
    description: str | None = None

    def column(self, name: str) -> ColumnInfo | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class SchemaInfo:
    schema: str
    tables: list[TableInfo] = field(default_factory=list)

    def table(self, name: str) -> TableInfo | None:
        wanted = name.strip().strip('"').lower()
        if "." in wanted:
            schema, _, wanted = wanted.rpartition(".")
            if schema.strip('"') != self.schema.lower():
                return None
        return next((t for t in self.tables if t.name.lower() == wanted), None)


@dataclass
class QueryColumn:
    name: str
    type: str


@dataclass
class QueryResult:
    columns: list[QueryColumn]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    execution_ms: int


@runtime_checkable
class DataConnector(Protocol):
    """A business data source. PostgreSQL today; MySQL/MSSQL/1C/REST/Excel later."""

    id: str
    type: str
    name: str
    dialect: str
    schema: str

    async def test_connection(self) -> ConnectionStatus: ...

    async def introspect(self) -> SchemaInfo: ...

    async def execute_readonly(
        self, sql: str, params: dict[str, Any] | None, max_rows: int, timeout_ms: int
    ) -> QueryResult: ...

    def public_details(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...
