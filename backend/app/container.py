"""Wiring of application components. Tests build a Container with fakes instead."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.agent.agent import DataAgent
from app.agent.date_context import today_in
from app.config import Settings
from app.connectors.base import DataConnector
from app.connectors.postgres import PostgresConnector
from app.ingest.contract import CanonicalContract, ContractError
from app.ingest.service import IngestService, parse_tokens
from app.llm.base import LLMProvider
from app.llm.factory import build_provider
from app.semantic.loader import SemanticError, SemanticLayer, load_semantic
from app.semantic.models import LocalizedText
from app.semantic.sources import SourceConfig, load_source_semantic, load_sources
from app.services.ask import ApiError, AskService
from app.services.dashboard import DashboardService
from app.services.insights import InsightsService
from app.services.metrics import MetricService
from app.sql.executor import SafeSqlExecutor
from app.sql.validator import SqlValidator
from app.storage.app_db import AppStore

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_ID = "demo_postgres"


@dataclass
class Services:
    semantic: SemanticLayer
    executor: SafeSqlExecutor
    metrics: MetricService
    dashboard: DashboardService
    insights: InsightsService
    agent: DataAgent
    ask: AskService


@dataclass
class DataSource:
    """One analyzable data source: its connector, semantic layer and services built on top of them."""

    id: str
    config: SourceConfig
    connector: DataConnector
    services: Services | None
    semantic_error: str | None = None

    def name(self, lang: str = "ru") -> str:
        return self.config.name.get(lang)

    def public(self, lang: str, default_id: str) -> dict[str, Any]:
        return {"id": self.id, "name": self.name(lang), "type": self.config.type, "storage": self.config.storage,
                "mode": self.config.mode, "is_default": self.id == default_id, "available": self.services is not None}


@dataclass
class Container:
    settings: Settings
    store: AppStore
    provider: LLMProvider
    sources: dict[str, DataSource]
    default_source_id: str
    ingest: IngestService | None = None
    background: set[asyncio.Task[Any]] = field(default_factory=set)

    @classmethod
    def single(cls, settings: Settings, connector: DataConnector, store: AppStore, provider: LLMProvider,
               services: Services | None, source_id: str = DEFAULT_SOURCE_ID, semantic_error: str | None = None) -> "Container":
        config = SourceConfig.model_validate({"type": connector.type, "storage": "direct", "mode": "demo",
                                              "schema": connector.schema, "semantic": ["."],
                                              "name": {"ru": connector.name, "en": connector.name}})
        source = DataSource(source_id, config, connector, services, semantic_error)
        return cls(settings, store, provider, {source_id: source}, source_id)

    # ------------------------------------------------------------------ source selection

    def source(self, source_id: str | None = None) -> DataSource:
        key = source_id or self.default_source_id
        found = self.sources.get(key)
        if found is None:
            raise ApiError(404, "not_found", f"data source '{key}' not found")
        return found

    @property
    def connector(self) -> DataConnector:
        return self.source().connector

    @property
    def services(self) -> Services | None:
        return self.source().services

    def require(self, source_id: str | None = None) -> Services:
        source = self.source(source_id)
        if source.services is None:
            raise ApiError(503, "internal_error", f"semantic layer is not available: {source.semantic_error}")
        return source.services

    def today(self) -> date:
        return today_in(self.settings.app_timezone)

    async def close(self) -> None:
        for source in self.sources.values():
            await source.connector.close()
        await self.store.close()
        if self.ingest is not None:
            await self.ingest.close()


def build_services(settings: Settings, semantic: SemanticLayer, connector: DataConnector, store: AppStore,
                   provider: LLMProvider, source: SourceConfig | None = None, source_id: str = DEFAULT_SOURCE_ID) -> Services:
    validator = SqlValidator(allowed_schema=connector.schema, max_rows=settings.sql_max_rows,
                             max_length=settings.sql_max_query_length, dialect=connector.dialect)
    executor = SafeSqlExecutor(connector, validator, max_rows=settings.sql_max_rows,
                               timeout_ms=settings.sql_statement_timeout_ms)
    metrics = MetricService(semantic, executor)
    dashboard = DashboardService(semantic, metrics)
    insights = InsightsService(semantic, metrics, executor, dashboard, provider, settings.insights_cache_seconds)
    agent = DataAgent(settings=settings, semantic=semantic, connector=connector, executor=executor, metrics=metrics,
                      provider=provider, source=source)
    ask = AskService(agent=agent, store=store, provider=provider, history_turns=settings.agent_history_turns,
                     source_id=source_id)
    return Services(semantic, executor, metrics, dashboard, insights, agent, ask)


def build_container(settings: Settings) -> Container:
    store = AppStore(settings.database_url.get_secret_value())
    provider = build_provider(settings)
    agent_url = settings.agent_database_url.get_secret_value()
    try:
        registry = load_sources(settings.semantic_dir)
    except SemanticError as exc:
        logger.error("data source registry failed to load", extra={"error": str(exc)})
        registry = None

    if registry is None:  # single-source layout: semantic_dir holds the six YAML files directly
        connector = PostgresConnector(agent_url, schema=settings.data_schema, source_id=DEFAULT_SOURCE_ID)
        try:
            semantic = load_semantic(settings.semantic_dir)
            return Container.single(settings, connector, store, provider,
                                    build_services(settings, semantic, connector, store, provider))
        except SemanticError as exc:
            logger.error("semantic layer failed to load", extra={"error": str(exc)})
            return Container.single(settings, connector, store, provider, None, semantic_error=str(exc))

    sources: dict[str, DataSource] = {}
    for source_id, config in registry.sources.items():
        connector = PostgresConnector(agent_url, schema=config.schema_name, source_id=source_id,
                                      name=config.name.get("ru"), max_pool_size=6)
        try:
            semantic = load_source_semantic(settings.semantic_dir, config)
        except SemanticError as exc:
            logger.error("semantic layer failed to load", extra={"source_id": source_id, "error": str(exc)})
            sources[source_id] = DataSource(source_id, config, connector, None, str(exc))
            continue
        services = build_services(settings, semantic, connector, store, provider, config, source_id)
        sources[source_id] = DataSource(source_id, config, connector, services)
        logger.info("data source ready", extra={"source_id": source_id, "storage": config.storage,
                                                "metrics": len(semantic.metrics.metrics)})

    ingest = None
    if any(s.storage == "canonical" for s in registry.sources.values()):
        try:
            contract = CanonicalContract.load(Path(settings.contracts_dir) / "canonical" / "v1.yaml")
            ingest = IngestService(settings.ingest_database_url.get_secret_value(), contract, registry.sources,
                                   parse_tokens(settings.ingest_tokens.get_secret_value()),
                                   settings.ingest_max_chunk_records)
        except ContractError as exc:
            logger.error("canonical contract failed to load", extra={"error": str(exc)})
    logger.info("application ready", extra={"llm_provider": provider.name, "llm_model": provider.model,
                                            "llm_configured": provider.configured, "sources": list(sources),
                                            "ingest_enabled": bool(ingest and ingest.enabled)})
    return Container(settings, store, provider, sources, registry.default, ingest)


def localized(text: LocalizedText | None, lang: str) -> str | None:
    return text.get(lang) if text else None
