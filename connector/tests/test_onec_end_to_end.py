"""Demo Foods rows → 1C infobase → OData emulator → 1C adapter → canonical records."""

from __future__ import annotations

import gzip
import json
import uuid
from datetime import date
from typing import Any

import httpx
import pytest

from biznext_connector.adapters import Window
from biznext_connector.biznext import BizNextClient
from biznext_connector.config import Settings
from biznext_connector.contract import Contract
from biznext_connector.onec.adapter import OneCAdapter
from biznext_connector.onec.mapping import Mapping
from biznext_connector.onec.odata import ODataClient
from biznext_connector.sync import SyncRunner, build_plan
from tests.conftest import CONTRACT, MAPPING, PASSWORD, TODAY

BASE = "http://onec/demo_foods/odata/standard.odata"


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


@pytest.fixture
def transport(emulator: Any) -> RecordingTransport:
    return RecordingTransport(httpx.ASGITransport(app=emulator.app))


def make_adapter(transport: httpx.AsyncBaseTransport, **kwargs: Any) -> OneCAdapter:
    client = ODataClient(BASE, "biznext_reader", PASSWORD, page_size=2, transport=transport)
    return OneCAdapter(client, Mapping.load(MAPPING), Contract.load(CONTRACT),
                       excluded={"customers": {"tax_id"}, "suppliers": {"tax_id"}}, **kwargs)


async def collect(adapter: OneCAdapter, entity: str, **kwargs: Any) -> list[dict[str, Any]]:
    contract = adapter.contract
    rows = []
    async for page in adapter.extract(entity, **kwargs):
        for raw in page:
            record = contract.coerce(entity, raw)
            assert contract.problems(entity, record) == [], (entity, record)
            rows.append(record)
    return rows


async def test_emulator_is_read_only_and_requires_auth(transport: RecordingTransport) -> None:
    async with httpx.AsyncClient(transport=transport) as http:
        assert (await http.get(f"{BASE}/Catalog_Номенклатура")).status_code == 401
        denied = await http.post(f"{BASE}/Catalog_Номенклатура", auth=("biznext_reader", PASSWORD), json={"Description": "x"})
        assert denied.status_code == 403 and "Только чтение" in denied.json()["odata.error"]["message"]["value"]
        bad = await http.get(f"{BASE}/Catalog_Номенклатура", auth=("biznext_reader", PASSWORD),
                             params={"$filter": "Оклад eq 1; DROP TABLE x"})
        assert bad.status_code == 400
    assert not any(hasattr(ODataClient, m) for m in ("post", "put", "patch", "delete"))


async def test_check_reports_published_objects(transport: RecordingTransport) -> None:
    adapter = make_adapter(transport)
    result = await adapter.check()
    assert result["ok"] and result["missing_objects"] == []
    assert result["counts"]["Document_РеализацияТоваровУслуг"] == 2  # the cancelled order is not posted
    await adapter.close()


async def test_catalogs(transport: RecordingTransport) -> None:
    adapter = make_adapter(transport)
    products = {p["name"]: p for p in await collect(adapter, "products")}
    assert products["Молоко 3,2% 1 л"]["item_type"] == "goods" and products["Молоко 3,2% 1 л"]["category"] == "Молоко"
    assert products["Молоко сырое"] == {**products["Молоко сырое"], "item_type": "material", "category": "Сырьё", "unit": "л"}
    customers = {c["name"]: c for c in await collect(adapter, "customers")}
    chain = customers["Сеть супермаркетов Алтын"]
    assert (chain["segment"], chain["region"], chain["macro_region"], chain["manager"]) == ("Розничная сеть", "Алматы", "Юг", "Иванова Асель")
    assert "tax_id" not in chain  # excluded by policy: not requested from 1C at all
    suppliers = await collect(adapter, "suppliers")
    assert [(s["name"], s["supplier_type"]) for s in suppliers] == [("Ферма Береке", "Сырьё")]
    warehouses = await collect(adapter, "warehouses")
    assert warehouses[0]["warehouse_type"] == "Склады завода" and warehouses[0]["region"] == "Алматы"
    employees = {e["name"]: e for e in await collect(adapter, "employees")}
    assert employees["Петров Олег"]["department"] == "Производство" and employees["Петров Олег"]["hired_at"] == "2021-03-01"
    assert employees["Петров Олег"]["terminated_at"] is None
    await adapter.close()

    urls = [str(r.url) for r in transport.requests]
    assert not any("%D0%9E%D0%BA%D0%BB%D0%B0%D0%B4" in u or "Оклад" in u for u in urls)  # salary never requested
    assert not any("%D0%91%D0%98%D0%9D" in u for u in urls)  # BIN (tax id) never requested
    staff = [r for r in transport.requests if "Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8" in str(r.url)]
    assert staff and all("%24select" in str(r.url) for r in staff)


async def test_sales_with_cost_allocation_and_returns(transport: RecordingTransport) -> None:
    adapter = make_adapter(transport)
    rows = await collect(adapter, "sales", window=Window(date(2026, 8, 1), date(2026, 9, 15)))
    sales = [r for r in rows if r["operation"] == "sale"]
    returns = [r for r in rows if r["operation"] == "return"]
    assert len(sales) == 4 and len(returns) == 1  # cancelled order excluded
    assert round(sum(r["amount"] for r in sales), 2) == 5000 + 15000 + 9000 + 4000
    assert round(sum(r["vat_amount"] for r in sales), 2) == round(33000 * 0.12, 2)
    first_order = sorted((r for r in sales if r["document_number"] == "SO-2026-000001"), key=lambda r: r["id"])
    milk = sorted((r["quantity"], r["cost_amount"]) for r in first_order if r["amount"] in (5000, 15000))
    assert milk == [(10, 100.0), (30, 300.0)]  # register cost 400 for the product split by line quantity
    assert {r["manager"] for r in sales} == {"Иванова Асель"} and all(r["customer_id"] for r in sales)
    assert returns[0]["amount"] == 1800 and returns[0]["base_document_id"] == first_order[0]["document_id"]
    assert all(r["due_date"] for r in sales)
    august = await collect(adapter, "sales", window=Window(date(2026, 8, 1), date(2026, 9, 1)))
    assert {r["date"][:7] for r in august} == {"2026-08"}
    await adapter.close()


async def test_documents_and_snapshots(transport: RecordingTransport) -> None:
    adapter = make_adapter(transport)
    window = Window(date(2026, 8, 1), date(2026, 9, 15))
    purchases = await collect(adapter, "purchases", window=window)
    assert purchases[0]["amount"] == 180500 and purchases[0]["quantity"] == 1000
    production = await collect(adapter, "production", window=window)
    assert production[0] | {} == {**production[0], "quantity": 98, "planned_quantity": 100, "defect_quantity": 2,
                                  "shift": "Дневная", "work_center": "Линия розлива №1"}
    costs = await collect(adapter, "costs", window=window)
    assert (costs[0]["cost_item"], costs[0]["department"], costs[0]["amount"]) == ("Логистика", "Администрация", 50000)
    write_offs = await collect(adapter, "write_offs", window=window)
    assert (write_offs[0]["reason"], write_offs[0]["cost_amount"]) == ("Истёк срок годности", 3900)
    payments = await collect(adapter, "payments", window=window)
    assert len(payments) == 1 and payments[0]["amount"] == round(29000 * 1.12, 2) and payments[0]["direction"] == "incoming"

    inventory = await collect(adapter, "inventory", snapshot_dates=[date(2026, 8, 31), TODAY])
    today_rows = {r["quantity"]: r for r in inventory if r["snapshot_date"] == TODAY.isoformat()}
    assert set(today_rows) == {380, 0}  # zero stock restored from the stock norm register
    assert today_rows[0]["reorder_point"] == 20
    receivables = await collect(adapter, "receivables", snapshot_dates=[TODAY])
    assert len(receivables) == 1 and receivables[0]["amount_due"] == round(4000 * 1.12, 2)
    assert receivables[0]["due_date"] == "2026-09-10"
    before_payment = await collect(adapter, "receivables", snapshot_dates=[date(2026, 9, 1)])
    assert len(before_payment) == 1 and before_payment[0]["amount_due"] == round(29000 * 1.12, 2)
    await adapter.close()


class FakeBizNext:
    """In-memory ingest API: records loads the way the backend would receive them."""

    def __init__(self) -> None:
        self.loads: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer connector-token"
        path = request.url.path.removeprefix("/api/ingest/v1")
        body = request.content
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        payload = json.loads(body) if body else None
        if path == "/state":
            return httpx.Response(200, json={"last_full_success_at": None})
        if path == "/runs":
            run_id = str(uuid.uuid4())
            self.runs[run_id] = {"kind": payload["kind"], "connector": payload["connector"]}
            return httpx.Response(200, json={"run_id": run_id})
        if path.endswith("/finish"):
            self.runs[path.split("/")[2]].update(payload)
            return httpx.Response(200, json={})
        if path == "/loads":
            load_id = str(uuid.uuid4())
            self.loads[load_id] = {**payload, "chunks": {}}
            return httpx.Response(200, json={"load_id": load_id})
        if path.endswith("/chunks"):
            self.loads[path.split("/")[2]]["chunks"][payload["seq"]] = payload["records"]
            return httpx.Response(200, json={"received": len(payload["records"])})
        if path.endswith("/commit"):
            load = self.loads[path.split("/")[2]]
            load["committed"] = sum(len(c) for c in load["chunks"].values())
            assert load["committed"] == payload["expected_records"]
            return httpx.Response(200, json={"inserted": load["committed"], "deleted": 0})
        return httpx.Response(404)


async def test_full_sync_run(transport: RecordingTransport, fixed_now: Any) -> None:
    fake = FakeBizNext()
    settings = Settings(biznext_url="http://biznext", token="connector-token", onec_url=BASE, onec_user="biznext_reader",
                        onec_password=PASSWORD, mapping_path=str(MAPPING), contract_path=str(CONTRACT), chunk_size=3)
    adapter = make_adapter(transport)
    biznext = BizNextClient("http://biznext", "connector-token", transport=httpx.MockTransport(fake))
    runner = SyncRunner(settings, Contract.load(CONTRACT), adapter, biznext)
    runner.now = lambda: fixed_now  # type: ignore[method-assign]
    result = await runner.run_once("auto")
    assert result["kind"] == "full" and result["status"] == "succeeded", result
    by_entity = {load["entity"]: load for load in fake.loads.values()}
    assert set(by_entity) == set(adapter.entities())
    assert by_entity["products"]["mode"] == "replace_all" and by_entity["products"]["window_from"] is None
    assert by_entity["sales"]["window_to"] == "2026-09-15" and by_entity["sales"]["committed"] == 5
    assert len(by_entity["sales"]["chunks"]) == 2  # chunk_size 3
    run = next(iter(fake.runs.values()))
    assert run["status"] == "succeeded" and run["connector"]["entities"]["customers"]["excluded_fields"] == ["tax_id"]
    assert PASSWORD not in json.dumps(run)
    await adapter.close()
    await biznext.close()


def test_plan_windows() -> None:
    contract = Contract.load(CONTRACT)

    class StubAdapter:
        def entities(self) -> list[str]:
            return ["sales", "products", "inventory", "receivables"]

        def snapshot_policy(self, entity: str) -> Any:
            from biznext_connector.adapters import SnapshotPolicy

            return SnapshotPolicy("month_end", 62 if entity == "inventory" else 1)

    full = {p.entity: p for p in build_plan(contract, StubAdapter(), full=True, today=TODAY, history_months=14, lookback_days=35)}  # type: ignore[arg-type]
    assert [p for p in full] == ["products", "sales", "inventory", "receivables"]
    assert full["sales"].window == Window(date(2025, 7, 1), date(2026, 9, 15))
    assert len(full["inventory"].snapshot_dates) == 62 + 12 and full["inventory"].snapshot_dates[-1] == TODAY
    assert date(2025, 7, 31) in full["receivables"].snapshot_dates and TODAY in full["receivables"].snapshot_dates
    incremental = {p.entity: p for p in build_plan(contract, StubAdapter(), full=False, today=TODAY, history_months=14, lookback_days=35)}  # type: ignore[arg-type]
    assert incremental["sales"].window == Window(date(2026, 8, 10), date(2026, 9, 15))
    assert incremental["inventory"].snapshot_dates == [date(2026, 9, 13), TODAY]
    assert incremental["receivables"].window == Window(TODAY, date(2026, 9, 15))
