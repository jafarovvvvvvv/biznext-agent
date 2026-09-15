"""Source adapter interface. 1C today; MySQL / MS SQL / Excel / Google Sheets / REST API implement the same."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol


@dataclass(frozen=True)
class Window:
    """Half-open date interval [start, end)."""

    start: date
    end: date


@dataclass(frozen=True)
class SnapshotPolicy:
    history: str = "month_end"  # which past dates to snapshot on a full sync
    recent_days: int = 1  # daily snapshots for the last N days (including today)


class SourceAdapter(Protocol):
    system: str

    def entities(self) -> list[str]:
        """Canonical entities this adapter can produce with its mapping."""
        ...

    def snapshot_policy(self, entity: str) -> SnapshotPolicy: ...

    def describe(self) -> dict[str, Any]:
        """Non-secret description for the BizNext UI: system, configuration, entity → source objects, policy."""
        ...

    async def check(self) -> dict[str, Any]:
        """Connectivity and availability of every mapped source object."""
        ...

    def extract(self, entity: str, *, window: Window | None = None,
                snapshot_dates: list[date] | None = None) -> AsyncIterator[list[dict[str, Any]]]:
        """Yield pages of canonical records (not yet type-coerced)."""
        ...

    async def close(self) -> None: ...
