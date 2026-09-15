from __future__ import annotations

import pytest

from biznext_connector.contract import Contract, coerce_value
from biznext_connector.onec.mapping import Lookups, MappingError, RowContext, _source_spec, evaluate, paths_of
from odata_filter import FilterError, to_sql
from tests.conftest import CONTRACT


def ctx(header: dict, line: dict | None = None) -> RowContext:
    lookups = Lookups()
    lookups.add("Catalog_Регионы", ("Ref_Key",), [{"Ref_Key": "A1", "Description": "Алматы", "Parent_Key": "G1"},
                                                  {"Ref_Key": "G1", "Description": "Юг", "Parent_Key": None}])
    lookups.add("Нормы", ("Склад_Key", "Номенклатура_Key"), [{"Склад_Key": "S", "Номенклатура_Key": "N", "Мин": 7}])
    return RowContext(header, line, lookups)


def test_expressions() -> None:
    row = ctx({"Ref_Key": "D1", "Регион_Key": "a1", "Сумма": 112.0, "Флаг": False, "Склад_Key": "S", "Номенклатура_Key": "N",
               "Вид": "Товар"}, {"LineNumber": "2", "Сумма": 224.0, "СуммаНДС": 24.0})
    assert evaluate({"format": "{Ref_Key}:{line.LineNumber}"}, row) == "D1:2"
    assert evaluate({"expr": "line.Сумма - line.СуммаНДС"}, row) == 200.0
    assert evaluate({"lookup": "Catalog_Регионы", "key": "Регион_Key", "attr": "Description"}, row) == "Алматы"
    assert evaluate({"lookup": "Catalog_Регионы", "key": "Регион_Key", "attr": "Parent_Key",
                     "then": {"lookup": "Catalog_Регионы", "attr": "Description"}}, row) == "Юг"
    assert evaluate({"lookup": "Нормы", "key": ["Склад_Key", "Номенклатура_Key"], "attr": "Мин"}, row) == 7
    assert evaluate({"not": "Флаг"}, row) is True
    assert evaluate({"coalesce": ["Нет", {"const": "x"}]}, row) == "x"
    assert evaluate({"path": "Вид", "map": {"Товар": "goods"}}, row) == "goods"
    assert paths_of({"expr": "line.Сумма - Сумма"}) == {"line.Сумма", "Сумма"}
    assert paths_of({"format": "{Ref_Key}:{line.LineNumber}"}) == {"Ref_Key", "line.LineNumber"}


@pytest.mark.parametrize("bad", [{"expr": "__import__('os')"}, {"expr": "line.a.b"}, {"lookup": "X"}, {"const": 1, "path": "a"}])
def test_invalid_expressions_rejected(bad: dict) -> None:
    with pytest.raises(MappingError):
        _source_spec("products", {"object": "Catalog_X", "fields": {"name": bad}})


def test_contract_coercion_and_problems() -> None:
    contract = Contract.load(CONTRACT)
    assert coerce_value("date", "0001-01-01T00:00:00") is None
    assert coerce_value("string", "00000000-0000-0000-0000-000000000000") is None
    assert coerce_value("date", "2026-09-14T00:00:00") == "2026-09-14"
    record = contract.coerce("sales", {"id": "1", "document_id": "d", "date": "2026-09-14T00:00:00", "operation": "gift",
                                      "product_id": "p", "quantity": "2", "amount": 10})
    assert record["quantity"] == 2.0
    assert contract.problems("sales", record) == ["'operation' must be one of ['sale', 'return'], got 'gift'"]
    assert "'amount' is required" in contract.problems("sales", {"id": "1"})
    assert contract.problems("products", {"id": "1", "name": "x", "item_type": "goods", "salary": 1}) == ["unknown field 'salary'"]


def test_filter_translation() -> None:
    sql, params = to_sql("Posted eq true and (Date ge datetime'2026-08-01T00:00:00' or Number eq 'A''B') and Ref_Key ne null",
                         {"Posted", "Date", "Number", "Ref_Key"})
    assert sql == '("Posted" = ? AND (("Date" >= ? OR "Number" = ?)) AND "Ref_Key" IS NOT NULL)'
    assert params == [1, "2026-08-01T00:00:00", "A'B"]
    for bad in ("Unknown eq 1", "Posted eq", "Posted like 1", "Date ge datetime'2026'", "Posted eq 1; DROP"):
        with pytest.raises(FilterError):
            to_sql(bad, {"Posted", "Date"})
