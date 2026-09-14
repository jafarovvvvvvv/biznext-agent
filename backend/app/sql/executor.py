from __future__ import annotations

import logging
from dataclasses import dataclass

from app.connectors.base import DataConnector, QueryResult, SqlExecutionError
from app.sql.validator import SqlValidator, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class ExecutionOutcome:
    validation: ValidationResult
    result: QueryResult | None = None
    error: SqlExecutionError | None = None

    @property
    def ok(self) -> bool:
        return self.validation.ok and self.error is None and self.result is not None

    @property
    def executed_sql(self) -> str | None:
        return self.validation.normalized_sql


class SafeSqlExecutor:
    """The only path to business data: validate → enforce LIMIT → read-only execution with timeout.

    DatabaseUnavailableError from the connector is not caught — callers map it to `database_unavailable`.
    """

    def __init__(self, connector: DataConnector, validator: SqlValidator, *, max_rows: int, timeout_ms: int) -> None:
        self.connector = connector
        self.validator = validator
        self.max_rows = max_rows
        self.timeout_ms = timeout_ms

    async def execute(self, sql: str, *, purpose: str, max_rows: int | None = None) -> ExecutionOutcome:
        limit = max_rows if max_rows is not None else self.max_rows
        validation = self.validator.validate(sql, max_rows=limit)
        logger.info(
            "sql validation",
            extra={"purpose": purpose, "ok": validation.ok, "reason": validation.reason_code, "sql": sql[:4000]},
        )
        if not validation.ok or validation.normalized_sql is None:
            return ExecutionOutcome(validation=validation)
        try:
            result = await self.connector.execute_readonly(
                validation.normalized_sql, None, max_rows=limit, timeout_ms=self.timeout_ms
            )
        except SqlExecutionError as exc:
            logger.info("sql execution failed", extra={"purpose": purpose, "code": exc.code, "error": exc.message})
            return ExecutionOutcome(validation=validation, error=exc)
        logger.info(
            "sql executed",
            extra={"purpose": purpose, "rows": result.row_count, "truncated": result.truncated, "ms": result.execution_ms},
        )
        return ExecutionOutcome(validation=validation, result=result)
