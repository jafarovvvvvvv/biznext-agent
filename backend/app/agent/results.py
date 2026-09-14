from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_NUMBER_IN_TEXT = re.compile(r"\d+(?:[.,]\d+)?")
NUMERIC_TYPES = {"int2", "int4", "int8", "numeric", "float4", "float8", "money", "smallint", "integer", "bigint",
                 "real", "double precision", "decimal"}
DATE_TYPES = {"date", "timestamp", "timestamptz", "timestamp with time zone", "timestamp without time zone"}


@dataclass
class ResultColumn:
    name: str
    type: str
    unit: str | None = None

    @property
    def is_numeric(self) -> bool:
        return self.type in NUMERIC_TYPES or self.type == "number"

    @property
    def is_date(self) -> bool:
        return self.type in DATE_TYPES or self.type == "date"


@dataclass
class StoredResult:
    result_id: str
    kind: str  # "sql" | "metric"
    purpose: str
    columns: list[ResultColumn]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    sql: list[dict[str, Any]] = field(default_factory=list)  # {sql, row_count, execution_ms}
    tables: list[str] = field(default_factory=list)
    metric: str | None = None

    def column(self, name: str) -> ResultColumn | None:
        return next((c for c in self.columns if c.name == name), None)


class ResultRegistry:
    """All data the agent obtained during one run; the final answer may only reference these results."""

    def __init__(self) -> None:
        self._results: dict[str, StoredResult] = {}

    def add(self, **kwargs: Any) -> StoredResult:
        result_id = f"r{len(self._results) + 1}"
        result = StoredResult(result_id=result_id, **kwargs)
        self._results[result_id] = result
        return result

    def get(self, result_id: str) -> StoredResult | None:
        return self._results.get(result_id)

    def all(self) -> list[StoredResult]:
        return list(self._results.values())

    def numeric_values(self, limit: int = 20000) -> list[float]:
        """Numbers the answer may cite: numeric cells plus numbers inside text cells (SKU names like '3,2% 1 л')."""
        values: list[float] = []
        for result in self._results.values():
            for row in result.rows:
                for value in row.values():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        values.append(float(value))
                    elif isinstance(value, str):
                        values += [float(n.replace(",", ".")) for n in _NUMBER_IN_TEXT.findall(value)]
                    if len(values) >= limit:
                        return values
        return values
