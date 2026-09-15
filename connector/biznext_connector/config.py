"""Connector settings from environment variables (see connector/README in docs/CONNECTORS.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_path(*parts: str) -> str:
    # connector/biznext_connector/config.py -> repository root (local runs) or /app (container)
    here = Path(__file__).resolve().parents[1]
    candidate = here.joinpath(*parts)
    if candidate.exists():
        return str(candidate)
    return str(here.parent.joinpath(*parts))


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    biznext_url: str
    token: str
    onec_url: str
    onec_user: str
    onec_password: str
    mapping_path: str
    contract_path: str
    timezone: str = "Asia/Almaty"
    sync_interval_minutes: int = 30
    full_sync_time: str = "03:40"
    history_months: int = 14
    lookback_days: int = 35
    page_size: int = 1000
    chunk_size: int = 2000
    http_timeout_seconds: float = 120.0
    verify_tls: bool = True
    entities: list[str] = field(default_factory=list)  # empty = everything in the mapping
    exclude_fields: list[str] = field(default_factory=lambda: ["customers.tax_id", "suppliers.tax_id"])

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            biznext_url=env.get("BIZNEXT_URL", "").rstrip("/"),
            token=env.get("CONNECTOR_TOKEN", ""),
            onec_url=env.get("ONEC_ODATA_URL", "").rstrip("/"),
            onec_user=env.get("ONEC_USER", ""),
            onec_password=env.get("ONEC_PASSWORD", ""),
            mapping_path=env.get("CONNECTOR_MAPPING") or _default_path("mappings", "1c_erp_demo.yaml"),
            contract_path=env.get("CONTRACT_PATH") or _default_path("contracts", "canonical", "v1.yaml"),
            timezone=env.get("CONNECTOR_TIMEZONE") or "Asia/Almaty",
            sync_interval_minutes=int(env.get("SYNC_INTERVAL_MINUTES") or 30),
            full_sync_time=env.get("FULL_SYNC_TIME") or "03:40",
            history_months=int(env.get("SYNC_HISTORY_MONTHS") or 14),
            lookback_days=int(env.get("SYNC_LOOKBACK_DAYS") or 35),
            page_size=int(env.get("ONEC_PAGE_SIZE") or 1000),
            chunk_size=int(env.get("INGEST_CHUNK_SIZE") or 2000),
            http_timeout_seconds=float(env.get("HTTP_TIMEOUT_SECONDS") or 120),
            verify_tls=(env.get("VERIFY_TLS") or "1") != "0",
            entities=_csv(env.get("CONNECTOR_ENTITIES", "")),
            exclude_fields=_csv(env.get("CONNECTOR_EXCLUDE_FIELDS", "customers.tax_id,suppliers.tax_id")),
        )

    def missing(self) -> list[str]:
        required = {"BIZNEXT_URL": self.biznext_url, "CONNECTOR_TOKEN": self.token, "ONEC_ODATA_URL": self.onec_url,
                    "ONEC_USER": self.onec_user, "ONEC_PASSWORD": self.onec_password}
        return [name for name, value in required.items() if not value]

    def secrets(self) -> list[str]:
        return [s for s in (self.token, self.onec_password) if s]

    def excluded(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for item in self.exclude_fields:
            entity, _, name = item.partition(".")
            if entity and name:
                result.setdefault(entity, set()).add(name)
        return result
