from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container, lang_param, source_param
from app.connectors.base import DatabaseUnavailableError
from app.container import Container
from app.services.ask import ApiError, error_message
from app.services.metrics import MetricQueryError

router = APIRouter()


@router.get("/dashboard")
async def dashboard(period: str | None = Query(None), container: Container = Depends(get_container),
                    lang: str = Depends(lang_param), source: str | None = Depends(source_param)) -> dict[str, Any]:
    services = container.require(source)
    try:
        return await services.dashboard.build(period, container.today(), lang)
    except DatabaseUnavailableError as exc:
        raise ApiError(503, "database_unavailable", error_message("database_unavailable", lang)) from exc
    except MetricQueryError as exc:
        raise ApiError(500, "internal_error", exc.message) from exc


@router.get("/insights")
async def insights(period: str | None = Query(None), container: Container = Depends(get_container),
                   lang: str = Depends(lang_param), source: str | None = Depends(source_param)) -> dict[str, Any]:
    services = container.require(source)
    try:
        return await services.insights.build(period, container.today(), lang)
    except DatabaseUnavailableError as exc:
        raise ApiError(503, "database_unavailable", error_message("database_unavailable", lang)) from exc
