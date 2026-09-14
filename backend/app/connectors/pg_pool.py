from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from app.connectors.base import DatabaseUnavailableError

logger = logging.getLogger(__name__)


class LazyPostgresPool:
    """Async psycopg pool that is opened on first use and re-created after connection failures.

    The application must start even when PostgreSQL is down; every use retries the connection.
    """

    def __init__(self, conninfo: str, *, name: str, max_size: int = 8, connect_timeout_s: int = 5) -> None:
        self._conninfo = conninfo
        self._name = name
        self._max_size = max_size
        self._connect_timeout_s = connect_timeout_s
        self._pool: AsyncConnectionPool | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._conninfo)

    async def get(self) -> AsyncConnectionPool:
        if not self._conninfo:
            raise DatabaseUnavailableError(f"{self._name}: connection string is not configured")
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is not None:
                return self._pool
            pool = AsyncConnectionPool(
                self._conninfo,
                min_size=1,
                max_size=self._max_size,
                open=False,
                name=self._name,
                timeout=10.0,
                kwargs={"autocommit": True, "connect_timeout": self._connect_timeout_s},
                check=AsyncConnectionPool.check_connection,
                reconnect_timeout=30.0,
            )
            try:
                await pool.open(wait=True, timeout=float(self._connect_timeout_s + 1))
            except (PoolTimeout, psycopg.Error, OSError) as exc:
                await self._safe_close(pool)
                logger.warning("database pool open failed", extra={"pool": self._name, "error": type(exc).__name__})
                raise DatabaseUnavailableError(f"{self._name}: database is unavailable") from exc
            self._pool = pool
            logger.info("database pool opened", extra={"pool": self._name})
            return pool

    async def invalidate(self) -> None:
        async with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            await self._safe_close(pool)

    async def close(self) -> None:
        await self.invalidate()

    @staticmethod
    async def _safe_close(pool: AsyncConnectionPool) -> None:
        try:
            await pool.close(timeout=2.0)
        except Exception:  # noqa: BLE001 - closing a broken pool must never raise
            pass


def json_safe(value: Any) -> Any:
    """Convert database values to JSON-friendly Python values."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<binary>"
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    return str(value)
