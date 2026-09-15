"""Read-only client for the 1C standard OData interface (standard.odata).

The class deliberately implements only HTTP GET: there is no code path that could create, change or
delete data in 1C. On the 1C side the publication user must additionally have a read-only role.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


class ODataError(RuntimeError):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body["odata.error"]["message"]["value"])
    except (ValueError, KeyError, TypeError):
        return response.text[:300] or response.reason_phrase


class ODataClient:
    def __init__(self, base_url: str, user: str, password: str, *, page_size: int = 1000, timeout: float = 120.0,
                 verify: bool = True, retries: int = 3, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.retries = retries
        self._client = httpx.AsyncClient(auth=(user, password), timeout=timeout, verify=verify, transport=transport,
                                         headers={"Accept": "application/json"})

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, str] | None = None, *, as_text: bool = False) -> Any:
        url = f"{self.base_url}/{quote(path, safe='/()=,$')}"
        query = {"$format": "json", **(params or {})}
        delay = 1.0
        for attempt in range(1, self.retries + 1):
            try:
                response = await self._client.get(url, params=query)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise ODataError(None, f"1C is unreachable: {type(exc).__name__}") from exc
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if response.status_code >= 500 and attempt < self.retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if response.status_code >= 400:
                raise ODataError(response.status_code, f"1C OData {response.status_code} for {path}: {_message(response)}")
            return response.text if as_text else response.json()
        raise ODataError(None, "unreachable")

    async def published_objects(self) -> tuple[set[str], str | None]:
        body = await self._get("")
        return {item["name"] for item in body.get("value", [])}, body.get("configuration")

    async def count(self, obj: str, filter_: str | None = None) -> int:
        params = {"$filter": filter_} if filter_ else {}
        return int(str(await self._get(f"{obj}/$count", params, as_text=True)).strip())

    async def pages(self, obj: str, *, filter_: str | None = None, select: list[str] | None = None,
                    order_by: str | None = "Ref_Key") -> AsyncIterator[list[dict[str, Any]]]:
        skip = 0
        while True:
            params = {"$top": str(self.page_size), "$skip": str(skip)}
            if filter_:
                params["$filter"] = filter_
            if select:
                params["$select"] = ",".join(select)
            if order_by:
                params["$orderby"] = order_by
            items = (await self._get(obj, params)).get("value", [])
            if items:
                yield items
            if len(items) < self.page_size:
                return
            skip += len(items)

    async def all(self, obj: str, **kwargs: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        async for page in self.pages(obj, **kwargs):
            rows.extend(page)
        return rows

    async def balance(self, register: str, period: str) -> list[dict[str, Any]]:
        """Virtual table of balances at the moment `period` (YYYY-MM-DDTHH:MM:SS)."""
        return (await self._get(f"{register}/Balance(Period=datetime'{period}')")).get("value", [])
