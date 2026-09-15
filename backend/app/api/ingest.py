"""Ingest API for BizNext Connectors (§56).

The connector inside the enterprise network calls these endpoints over outbound HTTPS with a per-source
bearer token. A token can write only into its own data source; every record is validated against the
canonical contract before it reaches staging, and each load replaces its window atomically.
"""

from __future__ import annotations

import json
import zlib
from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.connectors.base import DatabaseUnavailableError
from app.container import Container
from app.ingest.service import IngestError, IngestService
from app.services.ask import ApiError

router = APIRouter(prefix="/ingest/v1")

MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 96 * 1024 * 1024


class ConnectorCall:
    def __init__(self, service: IngestService, source_id: str) -> None:
        self.service = service
        self.source_id = source_id


def connector_call(request: Request, authorization: str | None = Header(default=None)) -> ConnectorCall:
    container: Container = request.app.state.container
    service = container.ingest
    if service is None or not service.enabled:
        raise ApiError(404, "not_found", "ingest API is not enabled")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "connector token required")
    source_id = service.authenticate(authorization[7:].strip())
    if source_id is None:
        raise ApiError(401, "unauthorized", "invalid connector token")
    return ConnectorCall(service, source_id)


async def _guard(coro: Any) -> Any:
    try:
        return await coro
    except IngestError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    except DatabaseUnavailableError as exc:
        raise ApiError(503, "database_unavailable", "ingest storage is unavailable") from exc


class RunStart(BaseModel):
    kind: Literal["full", "incremental", "check"]
    connector: dict[str, Any] = Field(default_factory=dict)


class RunFinish(BaseModel):
    status: Literal["succeeded", "failed"]
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=8000)


class LoadOpen(BaseModel):
    run_id: UUID
    entity: str = Field(max_length=64)
    mode: Literal["replace_all", "replace_window"]
    window_from: date | None = None
    window_to: date | None = None


class LoadCommit(BaseModel):
    expected_records: int = Field(ge=0)


@router.get("/contract")
async def contract(call: ConnectorCall = Depends(connector_call)) -> dict[str, Any]:
    return {"version": call.service.contract.version, "source_id": call.source_id,
            "entities": sorted(call.service.sources[call.source_id].entities or call.service.contract.entities)}


@router.get("/state")
async def state(call: ConnectorCall = Depends(connector_call)) -> dict[str, Any]:
    return await _guard(call.service.state(call.source_id))


@router.post("/runs")
async def start_run(body: RunStart, call: ConnectorCall = Depends(connector_call)) -> dict[str, Any]:
    run_id = await _guard(call.service.start_run(call.source_id, body.kind, body.connector))
    return {"run_id": run_id, "source_id": call.source_id}


@router.post("/runs/{run_id}/finish")
async def finish_run(run_id: UUID, body: RunFinish, call: ConnectorCall = Depends(connector_call)) -> dict[str, Any]:
    await _guard(call.service.finish_run(call.source_id, str(run_id), body.status, body.stats, body.error))
    return {"ok": True}


@router.post("/loads")
async def open_load(body: LoadOpen, call: ConnectorCall = Depends(connector_call)) -> dict[str, Any]:
    load_id = await _guard(call.service.open_load(call.source_id, str(body.run_id), body.entity, body.mode,
                                                  body.window_from, body.window_to))
    return {"load_id": load_id}


@router.post("/loads/{load_id}/chunks")
async def append_chunk(load_id: UUID, request: Request, call: ConnectorCall = Depends(connector_call)) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_COMPRESSED_BYTES:
        raise ApiError(413, "invalid_request", "chunk body is too large")
    if request.headers.get("content-encoding", "").lower() == "gzip":
        decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
        try:
            raw = decompressor.decompress(raw, MAX_JSON_BYTES)
        except zlib.error as exc:
            raise ApiError(400, "invalid_request", "chunk body is not valid gzip") from exc
        if decompressor.unconsumed_tail:
            raise ApiError(413, "invalid_request", "decompressed chunk is too large")
    try:
        payload = json.loads(raw)
        seq = int(payload["seq"])
        records = payload["records"]
        if not isinstance(records, list) or seq < 1:
            raise ValueError
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiError(400, "invalid_request", "chunk must be JSON {seq: int >= 1, records: [...]}") from exc
    received = await _guard(call.service.append_chunk(call.source_id, str(load_id), seq, records))
    return {"received": received}


@router.post("/loads/{load_id}/commit")
async def commit_load(load_id: UUID, body: LoadCommit, call: ConnectorCall = Depends(connector_call)) -> dict[str, Any]:
    return await _guard(call.service.commit(call.source_id, str(load_id), body.expected_records))
