"""Health, meta, suggested questions and data sources."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.agent.date_context import DateContext
from app.api.deps import get_container, lang_param
from app.config import APP_NAME, APP_VERSION
from app.connectors.base import DatabaseUnavailableError, SqlExecutionError
from app.container import Container
from app.services.ask import ApiError

router = APIRouter()

FUTURE_CONNECTORS = [
    ("1c", "1c", "1С:Предприятие"),
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
async def meta(container: Container = Depends(get_container), lang: str = Depends(lang_param)) -> dict[str, Any]:
    services = container.require()
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
    }


@router.get("/suggested-questions")
async def suggested_questions(container: Container = Depends(get_container),
                              lang: str = Depends(lang_param)) -> dict[str, Any]:
    services = container.require()
    values = DateContext(container.today()).placeholders(lang)
    questions = []
    for item in services.semantic.suggested_questions.questions:
        text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), item.text.get(lang))
        questions.append({"text": text, "category": item.category})
    return {"questions": questions}


@router.get("/data-sources")
async def data_sources(container: Container = Depends(get_container), lang: str = Depends(lang_param)) -> dict[str, Any]:
    connector = container.connector
    status = await connector.test_connection()
    details: dict[str, Any] = {**connector.public_details(), "checked_at": status.checked_at.isoformat(timespec="seconds")}
    if status.ok:
        try:
            schema = await connector.introspect()
            details["tables"] = len(schema.tables)
            details["total_rows"] = sum(t.row_count or 0 for t in schema.tables)
            if container.services is not None:
                data_range = await container.services.metrics.data_range()
                if data_range:
                    details["data_start"], details["data_end"] = data_range[0].isoformat(), data_range[1].isoformat()
        except (DatabaseUnavailableError, SqlExecutionError):
            status.ok = False
    description = ("Demo company database (synthetic data)" if lang == "en"
                   else "Демо-база предприятия (synthetic data)")
    sources: list[dict[str, Any]] = [{
        "id": connector.id, "type": connector.type, "name": connector.name,
        "status": "connected" if status.ok else "error", "description": description, "details": details,
    }]
    sources += [{"id": sid, "type": stype, "name": name, "status": "coming_soon"} for sid, stype, name in FUTURE_CONNECTORS]
    return {"sources": sources}


@router.get("/data-sources/{source_id}/tables")
async def data_source_tables(source_id: str, container: Container = Depends(get_container),
                             lang: str = Depends(lang_param)) -> dict[str, Any]:
    if source_id != container.connector.id:
        raise ApiError(404, "not_found", "data source not found")
    try:
        schema = await container.connector.introspect()
    except DatabaseUnavailableError as exc:
        raise ApiError(503, "database_unavailable", "database is unavailable") from exc
    dictionary = container.services.semantic.data_dictionary.tables if container.services else {}
    tables = []
    for table in schema.tables:
        doc = dictionary.get(table.name)
        columns = []
        for column in table.columns:
            column_doc = doc.columns.get(column.name) if doc else None
            description = (column_doc.description.get(lang) if column_doc and column_doc.description else None) or column.description
            columns.append({"name": column.name, "type": column.type, "description": description})
        tables.append({"name": table.name,
                       "description": (doc.description.get(lang) if doc and doc.description else None) or table.description,
                       "row_count": table.row_count, "columns": columns})
    return {"tables": tables, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
