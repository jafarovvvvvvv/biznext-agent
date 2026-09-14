"""Wiring of application components. Tests build a Container with fakes instead."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.agent.agent import DataAgent
from app.agent.date_context import today_in
from app.config import Settings
from app.connectors.base import DataConnector
from app.connectors.postgres import PostgresConnector
from app.llm.base import LLMProvider
from app.llm.factory import build_provider
from app.semantic.loader import SemanticError, SemanticLayer, load_semantic
from app.services.ask import ApiError, AskService
from app.services.dashboard import DashboardService
from app.services.insights import InsightsService
from app.services.metrics import MetricService
from app.sql.executor import SafeSqlExecutor
from app.sql.validator import SqlValidator
from app.storage.app_db import AppStore

logger = logging.getLogger(__name__)


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
class Container:
    settings: Settings
    connector: DataConnector
    store: AppStore
    provider: LLMProvider
    services: Services | None
    semantic_error: str | None = None
    background: set[asyncio.Task[Any]] = field(default_factory=set)

    def today(self) -> date:
        return today_in(self.settings.app_timezone)

    def require(self) -> Services:
        if self.services is None:
            raise ApiError(503, "internal_error", f"semantic layer is not available: {self.semantic_error}")
        return self.services

    async def close(self) -> None:
        await self.connector.close()
        await self.store.close()


def build_services(settings: Settings, semantic: SemanticLayer, connector: DataConnector, store: AppStore,
                   provider: LLMProvider) -> Services:
    validator = SqlValidator(allowed_schema=settings.data_schema, max_rows=settings.sql_max_rows,
                             max_length=settings.sql_max_query_length, dialect=connector.dialect)
    executor = SafeSqlExecutor(connector, validator, max_rows=settings.sql_max_rows,
                               timeout_ms=settings.sql_statement_timeout_ms)
    metrics = MetricService(semantic, executor)
    dashboard = DashboardService(semantic, metrics)
    insights = InsightsService(semantic, metrics, executor, dashboard, provider, settings.insights_cache_seconds)
    agent = DataAgent(settings=settings, semantic=semantic, connector=connector, executor=executor, metrics=metrics,
                      provider=provider)
    ask = AskService(agent=agent, store=store, provider=provider, history_turns=settings.agent_history_turns)
    return Services(semantic, executor, metrics, dashboard, insights, agent, ask)


def build_container(settings: Settings) -> Container:
    connector = PostgresConnector(settings.agent_database_url.get_secret_value(), schema=settings.data_schema)
    store = AppStore(settings.database_url.get_secret_value())
    provider = build_provider(settings)
    try:
        semantic = load_semantic(settings.semantic_dir)
    except SemanticError as exc:
        logger.error("semantic layer failed to load", extra={"error": str(exc)})
        return Container(settings, connector, store, provider, None, semantic_error=str(exc))
    services = build_services(settings, semantic, connector, store, provider)
    logger.info("application ready", extra={"llm_provider": provider.name, "llm_model": provider.model,
                                            "llm_configured": provider.configured,
                                            "metrics": len(semantic.metrics.metrics)})
    return Container(settings, connector, store, provider, services)
