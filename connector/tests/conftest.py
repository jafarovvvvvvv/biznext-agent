from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "connector", ROOT / "onec_demo", ROOT / "database" / "seed"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

TODAY = date(2026, 9, 14)
CONTRACT = ROOT / "contracts" / "canonical" / "v1.yaml"
MAPPING = ROOT / "connector" / "mappings" / "1c_erp_demo.yaml"
PASSWORD = "reader-secret"


class FakeCursor:
    """Answers the export queries of database/seed/onec_export.py with a tiny Demo Foods dataset."""

    d_aug = date(2026, 8, 10)
    d_sep = date(2026, 9, 3)

    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []
        self.answers: list[tuple[str, list[tuple[Any, ...]]]] = [
            ("FROM demo.product_categories", [(1, "Молоко"), (4, "Сыры")]),
            ("FROM demo.products", [(1, "MLK-1", "Молоко 3,2% 1 л", 1, "л", 7, True),
                                    (2, "CHS-2", "Сыр Российский 200 г", 4, "кг", 60, True)]),
            ("FROM demo.raw_materials", [(1, "Молоко сырое", "л", "Сырьё")]),
            ("FROM demo.regions", [(1, "Алматы", "Юг"), (2, "Астана", "Север")]),
            ("FROM demo.employees", [(1, "Иванова Асель", "Отдел продаж", "Менеджер по продажам", date(2020, 1, 1), None, 450000.0),
                                     (2, "Петров Олег", "Производство", "Оператор линии", date(2021, 3, 1), None, 380000.0)]),
            ("SELECT DISTINCT sales_manager_id", [(1,)]),
            ("FROM demo.production_lines", [(1, "Линия розлива №1", 1)]),
            ("SELECT DISTINCT department FROM demo.operating_expenses", [("Администрация",)]),
            ("FROM demo.warehouses", [(1, "Склад завода Алматы", 1, "plant", 1200)]),
            ("FROM demo.customers", [(1, "Сеть супермаркетов Алтын", "Розничная сеть", 1, 1, 30, 5e6, date(2024, 1, 1), True),
                                     (2, "Кафе Таң", "HoReCa", 2, 1, 14, 1e6, date(2024, 5, 1), True)]),
            ("FROM demo.suppliers", [(1, "Ферма Береке", 1, "Сырьё", 10)]),
            ("SELECT DISTINCT expense_category", [("Логистика",)]),
            ("SELECT max(order_date)", [(self.d_sep,)]),
            ("FROM demo.sales_orders ORDER BY", [
                (1, "SO-2026-000001", 1, 1, 1, self.d_aug, "delivered", date(2026, 9, 9), date(2026, 9, 5)),
                (2, "SO-2026-000002", 2, 1, 1, self.d_aug, "cancelled", date(2026, 8, 24), None),
                (3, "SO-2026-000003", 2, 1, 1, self.d_sep, "delivered", date(2026, 9, 10), None)]),
            ("FROM demo.sales_order_items", [
                (1, 1, 1, 10, 500.0, 0.0, 5000.0, 100.0),
                (1, 2, 1, 30, 500.0, 0.0, 15000.0, 300.0),
                (1, 3, 2, 5, 2000.0, 10.0, 9000.0, 6000.0),
                (2, 4, 1, 7, 500.0, 0.0, 3500.0, 70.0),
                (3, 5, 2, 2, 2000.0, 0.0, 4000.0, 2600.0)]),
            ("FROM demo.sales_returns", [(1, 1, 2, date(2026, 8, 20), 1, "Производственный брак", 1800.0)]),
            ("FROM demo.purchase_order_items", [(1, 1, 1000.0, 180.5, 180500.0)]),
            ("FROM demo.purchase_orders", [(1, "PO-2026-00001", 1, 1, self.d_aug, self.d_aug, "delivered")]),
            ("FROM demo.production_batches", [(1, 1, 1, self.d_aug, "day", 100, 98, 2, 8.0)]),
            ("FROM demo.write_offs", [(1, self.d_sep, 1, 2, 3, "Истёк срок годности", 3900.0)]),
            ("FROM demo.operating_expenses", [(1, self.d_aug, "Логистика", "Администрация", 50000.0, "Доставка клиентам")]),
            ("FROM demo.inventory_snapshots", [
                (date(2026, 8, 31), 1, 1, 400, 100), (date(2026, 8, 31), 1, 2, 50, 20),
                (TODAY, 1, 1, 380, 100), (TODAY, 1, 2, 0, 20)]),
        ]

    def execute(self, sql: str) -> None:
        for marker, rows in self.answers:
            if marker in sql:
                self.rows = rows
                return
        raise AssertionError(f"unexpected export query: {sql}")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class FakeConnection:
    def cursor(self) -> FakeCursor:
        return FakeCursor()


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def infobase(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import onec_export

    path = tmp_path_factory.mktemp("infobase") / "demo_1c.sqlite"
    onec_export.export(FakeConnection(), path)
    return path


@pytest.fixture
def emulator(infobase: Path) -> ModuleType:
    module = _load_module("onec_emulator_app", ROOT / "onec_demo" / "app.py")
    module.INFOBASE_PATH = infobase
    module.PASSWORD = PASSWORD
    return module


@pytest.fixture
def fixed_now() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime(2026, 9, 14, 12, 0, tzinfo=ZoneInfo("Asia/Almaty"))
