"""Client of the BizNext ingest API (outbound HTTPS from the enterprise network; no inbound ports needed)."""

from __future__ import annotations

import asyncio
import gzip
import json
from typing import Any

import httpx

from biznext_connector import VERSION


class IngestError(RuntimeError):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


class BizNextClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 120.0, verify: bool = True, retries: int = 4,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.retries = retries
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api/ingest/v1", timeout=timeout, verify=verify, transport=transport,
            headers={"Authorization": f"Bearer {token}", "User-Agent": f"biznext-connector/{VERSION}"})

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, payload: Any = None, compress: bool = False) -> Any:
        headers = {"Content-Type": "application/json"}
        content: bytes | None = None
        if payload is not None:
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if compress:
                content = gzip.compress(content, compresslevel=5)
                headers["Content-Encoding"] = "gzip"
        delay = 1.0
        for attempt in range(1, self.retries + 1):
            try:
                response = await self._client.request(method, path, content=content, headers=headers)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise IngestError(None, f"BizNext is unreachable: {type(exc).__name__}") from exc
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if response.status_code >= 500 and attempt < self.retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if response.status_code >= 400:
                try:
                    message = response.json()["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    message = response.text[:300]
                raise IngestError(response.status_code, f"BizNext ingest {response.status_code} on {path}: {message}")
            return response.json() if response.content else {}
        raise IngestError(None, "unreachable")

    async def state(self) -> dict[str, Any]:
        return await self._request("GET", "/state")

    async def start_run(self, kind: str, connector: dict[str, Any]) -> str:
        return (await self._request("POST", "/runs", payload={"kind": kind, "connector": connector}))["run_id"]

    async def finish_run(self, run_id: str, status: str, stats: dict[str, Any], error: str | None = None) -> None:
        await self._request("POST", f"/runs/{run_id}/finish", payload={"status": status, "stats": stats, "error": error})

    async def open_load(self, run_id: str, entity: str, mode: str, window_from: str | None, window_to: str | None) -> str:
        body = {"run_id": run_id, "entity": entity, "mode": mode, "window_from": window_from, "window_to": window_to}
        return (await self._request("POST", "/loads", payload=body))["load_id"]

    async def send_chunk(self, load_id: str, seq: int, records: list[dict[str, Any]]) -> int:
        body = {"seq": seq, "records": records}
        return int((await self._request("POST", f"/loads/{load_id}/chunks", payload=body, compress=True))["received"])

    async def commit(self, load_id: str, expected: int) -> dict[str, Any]:
        return await self._request("POST", f"/loads/{load_id}/commit", payload={"expected_records": expected})
