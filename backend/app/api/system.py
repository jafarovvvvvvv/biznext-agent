"""Health, meta, suggested questions and data sources."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.agent.date_context import DateContext
from app.api.deps import get_container, lang_param, source_param
from app.config import APP_NAME, APP_VERSION
from app.connectors.base import DatabaseUnavailableError, SqlExecutionError
from app.container import Container, DataSource
from app.services.ask import ApiError

router = APIRouter()

FUTURE_CONNECTORS = [
    ("excel", "excel", "Excel"),
    ("google_sheets", "google_sheets", "Google Sheets"),
    ("rest_api", "rest_api", "REST API"),
    ("mysql", "mysql", "MySQL"),
    ("mssql", "mssql", "MS SQL Server"),
]


@router.get("/health")
async def health(container: Container = Depends(get_container)) -> dict[str, Any]:
    status = await container.connector.test_connection()
    store_ok = await container.store.ping() if status.ok else False
    database_ok = status.ok and store_ok
    llm = "configured" if container.provider.configured else "not_configured"
    overall = "ok" if database_ok and container.services is not None and container.provider.configured else "degraded"
    return {"status": overall, "database": "ok" if database_ok else "error", "llm": llm}


@router.get("/meta")
async def meta(container: Container = Depends(get_container), lang: str = Depends(lang_param),
               source: str | None = Depends(source_param)) -> dict[str, Any]:
    selected = container.source(source)
    services = container.require(source)
    ctx = services.semantic.business_context
    try:
        data_range = await services.metrics.data_range()
    except (DatabaseUnavailableError, Exception):  # noqa: BLE001 - meta must render without the database
        data_range = None
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "environment": container.settings.app_environment,
        "company": {"name": ctx.company.name.get(lang), "industry": ctx.company.industry.get(lang) if ctx.company.industry else "",
                    "currency": ctx.currency.code, "currency_symbol": ctx.currency.symbol},
        "timezone": container.settings.app_timezone,
        "today": container.today().isoformat(),
        "data_range": {"start": data_range[0].isoformat(), "end": data_range[1].isoformat()} if data_range else None,
        "llm": {"provider": container.provider.name, "model": container.provider.model,
                "configured": container.provider.configured},
        "features": {"admin_token_required": bool(container.settings.admin_token_value)},
        "source": selected.public(lang, container.default_source_id),
        "sources": [s.public(lang, container.default_source_id) for s in container.sources.values()],
        "default_source": container.default_source_id,
    }


@router.get("/suggested-questions")
async def suggested_questions(container: Container = Depends(get_container), lang: str = Depends(lang_param),
                              source: str | None = Depends(source_param)) -> dict[str, Any]:
    services = container.require(source)
    values = DateContext(container.today()).placeholders(lang)
    questions = []
    for item in services.semantic.suggested_questions.questions:
        text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), item.text.get(lang))
        questions.append({"text": text, "category": item.category})
    return {"questions": questions}


async def _describe_source(container: Container, source: DataSource, lang: str) -> dict[str, Any]:
    connector = source.connector
    status = await connector.test_connection()
    details: dict[str, Any] = {**connector.public_details(), "checked_at": status.checked_at.isoformat(timespec="seconds")}
    empty = False
    if status.ok:
        try:
            schema = await connector.introspect()
            details["tables"] = len(schema.tables)
            details["total_rows"] = sum(t.row_count or 0 for t in schema.tables)
            if source.services is not None:
                data_range = await source.services.metrics.data_range()
                if data_range:
                    details["data_start"], details["data_end"] = data_range[0].isoformat(), data_range[1].isoformat()
                else:
                    empty = True
        except (DatabaseUnavailableError, SqlExecutionError):
            status.ok = False
    config = source.config
    item: dict[str, Any] = {
        "id": source.id, "type": config.type, "name": source.name(lang), "storage": config.storage, "mode": config.mode,
        "is_default": source.id == container.default_source_id,
        "status": "error" if not status.ok else ("waiting_for_sync" if empty and config.storage == "canonical" else "connected"),
        "description": config.description.get(lang) if config.description else None, "details": details,
    }
    if config.storage == "canonical" and container.ingest is not None:
        try:
            item["sync"] = await container.ingest.status(source.id)
            item["sync"]["ingest_enabled"] = container.ingest.enabled
        except DatabaseUnavailableError:
            item["sync"] = None
    return item


@router.get("/data-sources")
async def data_sources(container: Container = Depends(get_container), lang: str = Depends(lang_param)) -> dict[str, Any]:
    sources = [await _describe_source(container, source, lang) for source in container.sources.values()]
    sources += [{"id": sid, "type": stype, "name": name, "status": "coming_soon"} for sid, stype, name in FUTURE_CONNECTORS]
    return {"sources": sources}


@router.get("/data-sources/{source_id}/tables")
async def data_source_tables(source_id: str, container: Container = Depends(get_container),
                             lang: str = Depends(lang_param)) -> dict[str, Any]:
    source = container.source(source_id)
    try:
        schema = await source.connector.introspect()
    except DatabaseUnavailableError as exc:
        raise ApiError(503, "database_unavailable", "database is unavailable") from exc
    dictionary = source.services.semantic.data_dictionary.tables if source.services else {}
    tables = []
    for table in schema.tables:
        doc = dictionary.get(table.name)
        columns = []
        for column in table.columns:
            if column.name.startswith("_"):
                continue  # technical load columns (_run_id, _loaded_at)
            column_doc = doc.columns.get(column.name) if doc else None
            description = (column_doc.description.get(lang) if column_doc and column_doc.description else None) or column.description
            columns.append({"name": column.name, "type": column.type, "description": description})
        tables.append({"name": table.name,
                       "description": (doc.description.get(lang) if doc and doc.description else None) or table.description,
                       "row_count": table.row_count, "columns": columns})
    return {"tables": tables, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
