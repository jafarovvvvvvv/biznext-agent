"""Agent questions (JSON + SSE), conversation history, reports and traces."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_container, require_admin
from app.connectors.base import DatabaseUnavailableError
from app.container import Container
from app.services.ask import ApiError, error_message

logger = logging.getLogger(__name__)
router = APIRouter()

KEEPALIVE_SECONDS = 15


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    lang: Literal["ru", "en"] = "ru"


def _store_errors(exc: Exception) -> ApiError:
    return ApiError(503, "database_unavailable", "application storage is unavailable")


@router.post("/agent/ask")
async def ask(body: AskRequest, container: Container = Depends(get_container)) -> dict[str, Any]:
    services = container.require()
    try:
        return await services.ask.ask(body.question, body.conversation_id, body.lang, container.today())
    except DatabaseUnavailableError as exc:
        raise ApiError(503, "database_unavailable", error_message("database_unavailable", body.lang)) from exc


@router.post("/agent/ask/stream")
async def ask_stream(body: AskRequest, container: Container = Depends(get_container)) -> StreamingResponse:
    services = container.require()
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def runner() -> None:
        try:
            response = await services.ask.ask(body.question, body.conversation_id, body.lang, container.today(), emit=emit)
            await queue.put(("final", response))
        except ApiError as exc:
            await queue.put(("error", {"code": exc.code, "message": exc.message, "trace_id": exc.trace_id}))
        except DatabaseUnavailableError:
            await queue.put(("error", {"code": "database_unavailable",
                                       "message": error_message("database_unavailable", body.lang), "trace_id": None}))
        except Exception:  # noqa: BLE001 - the stream must always end with an event
            logger.exception("agent stream failed")
            await queue.put(("error", {"code": "internal_error", "message": error_message("internal_error", body.lang),
                                       "trace_id": None}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(runner())
    container.background.add(task)
    task.add_done_callback(container.background.discard)

    async def stream() -> AsyncIterator[str]:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            event, data = item
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.get("/conversations")
async def conversations(container: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        return {"conversations": await container.store.list_conversations()}
    except DatabaseUnavailableError as exc:
        raise _store_errors(exc) from exc


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: str, container: Container = Depends(get_container)) -> dict[str, Any]:
    _check_uuid(conversation_id)
    try:
        found = await container.store.get_conversation(conversation_id)
    except DatabaseUnavailableError as exc:
        raise _store_errors(exc) from exc
    if found is None:
        raise ApiError(404, "not_found", "conversation not found")
    return found


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, container: Container = Depends(get_container)) -> Response:
    _check_uuid(conversation_id)
    try:
        deleted = await container.store.delete_conversation(conversation_id)
    except DatabaseUnavailableError as exc:
        raise _store_errors(exc) from exc
    if not deleted:
        raise ApiError(404, "not_found", "conversation not found")
    return Response(status_code=204)


@router.get("/reports")
async def reports(limit: int = Query(50, ge=1, le=200), container: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        return {"reports": await container.store.list_reports(limit)}
    except DatabaseUnavailableError as exc:
        raise _store_errors(exc) from exc


@router.get("/traces", dependencies=[Depends(require_admin)])
async def traces(limit: int = Query(50, ge=1, le=500), container: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        return {"traces": await container.store.list_traces(limit)}
    except DatabaseUnavailableError as exc:
        raise _store_errors(exc) from exc


@router.get("/traces/{trace_id}")
async def trace(trace_id: str, container: Container = Depends(get_container)) -> dict[str, Any]:
    _check_uuid(trace_id)
    try:
        found = await container.store.get_trace(trace_id)
    except DatabaseUnavailableError as exc:
        raise _store_errors(exc) from exc
    if found is None:
        raise ApiError(404, "not_found", "trace not found")
    return found


def _check_uuid(value: str) -> None:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(404, "not_found", "not found") from exc
