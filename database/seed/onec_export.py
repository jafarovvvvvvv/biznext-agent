"""Export Demo Foods as a 1C:Enterprise infobase snapshot for the 1C OData emulator (demo mode, §56).

The emulator (service `onec-demo`) plays the role of the enterprise's 1C: it publishes these objects
through the standard OData interface and has no access to the BizNext database. The file shape
follows 1C conventions: GUID references (`Ref_Key`, `*_Key`), catalogs with folders, documents with
tabular sections, `Posted` / `DeletionMark`, amounts including 12% VAT, accumulation registers.

Usage: python onec_export.py  (reads SEED_DATABASE_URL, writes ONEC_INFOBASE_PATH)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

NAMESPACE = uuid.UUID("6f1c0a2e-6c9b-4a55-9d0b-5b1e0c1a7e11")
EMPTY_REF = "00000000-0000-0000-0000-000000000000"
EMPTY_DATE = "0001-01-01T00:00:00"
VAT_RATE = 0.12
CONFIGURATION = "Demo Foods · 1С:ERP (эмуляция для демо-режима)"

HEADER_CATALOG = [("Ref_Key", "TEXT PRIMARY KEY"), ("DataVersion", "TEXT"), ("DeletionMark", "INTEGER"),
                  ("Code", "TEXT"), ("Description", "TEXT"), ("IsFolder", "INTEGER"), ("Parent_Key", "TEXT")]
HEADER_DOCUMENT = [("Ref_Key", "TEXT PRIMARY KEY"), ("DataVersion", "TEXT"), ("DeletionMark", "INTEGER"),
                   ("Number", "TEXT"), ("Date", "TEXT"), ("Posted", "INTEGER")]
LINE = [("Ref_Key", "TEXT"), ("LineNumber", "INTEGER")]

# object name -> (kind, header attributes, {tabular section: attributes})
OBJECTS: dict[str, tuple[str, list[tuple[str, str]], dict[str, list[tuple[str, str]]]]] = {
    "Catalog_ВидыНоменклатуры": ("catalog", [("ТипНоменклатуры", "TEXT")], {}),
    "Catalog_ТоварныеКатегории": ("catalog", [], {}),
    "Catalog_УпаковкиЕдиницыИзмерения": ("catalog", [], {}),
    "Catalog_Номенклатура": ("catalog", [("Артикул", "TEXT"), ("ВидНоменклатуры_Key", "TEXT"),
                                         ("ТоварнаяКатегория_Key", "TEXT"), ("ЕдиницаИзмерения_Key", "TEXT"),
                                         ("СрокГодности", "INTEGER")], {}),
    "Catalog_БизнесРегионы": ("catalog", [], {}),
    "Catalog_Пользователи": ("catalog", [("Сотрудник_Key", "TEXT")], {}),
    "Catalog_Партнеры": ("catalog", [("Клиент", "INTEGER"), ("Поставщик", "INTEGER"), ("БизнесРегион_Key", "TEXT"),
                                     ("ОсновнойМенеджер_Key", "TEXT"), ("БИН", "TEXT"), ("ДатаРегистрации", "TEXT"),
                                     ("СрокОплатыДней", "INTEGER"), ("КредитныйЛимит", "REAL")], {}),
    "Catalog_Склады": ("catalog", [("БизнесРегион_Key", "TEXT"), ("Вместимость", "INTEGER")], {}),
    "Catalog_СтруктураПредприятия": ("catalog", [("ПроизводственноеПодразделение", "INTEGER")], {}),
    "Catalog_Должности": ("catalog", [], {}),
    "Catalog_Сотрудники": ("catalog", [("Подразделение_Key", "TEXT"), ("Должность_Key", "TEXT"), ("ДатаПриема", "TEXT"),
                                       ("ДатаУвольнения", "TEXT"), ("Оклад", "REAL")], {}),
    "Catalog_СтатьиРасходов": ("catalog", [], {}),
    "Document_РеализацияТоваровУслуг": ("document", [
        ("Партнер_Key", "TEXT"), ("Склад_Key", "TEXT"), ("Менеджер_Key", "TEXT"), ("ДатаПлатежа", "TEXT"),
        ("СуммаДокумента", "REAL"), ("Комментарий", "TEXT")], {
        "Товары": [("Номенклатура_Key", "TEXT"), ("Количество", "REAL"), ("Цена", "REAL"),
                   ("ПроцентРучнойСкидки", "REAL"), ("СтавкаНДС", "TEXT"), ("Сумма", "REAL"), ("СуммаНДС", "REAL")]}),
    "Document_ВозвратТоваровОтКлиента": ("document", [
        ("Партнер_Key", "TEXT"), ("Склад_Key", "TEXT"), ("Менеджер_Key", "TEXT"), ("ДокументРеализации_Key", "TEXT"),
        ("СуммаДокумента", "REAL")], {
        "Товары": [("Номенклатура_Key", "TEXT"), ("Количество", "REAL"), ("Сумма", "REAL"), ("СуммаНДС", "REAL"),
                   ("ПричинаВозврата", "TEXT")]}),
    "Document_ПоступлениеТоваровУслуг": ("document", [
        ("Партнер_Key", "TEXT"), ("Склад_Key", "TEXT"), ("ДатаПоступления", "TEXT"), ("СуммаДокумента", "REAL")], {
        "Товары": [("Номенклатура_Key", "TEXT"), ("Количество", "REAL"), ("Цена", "REAL"), ("Сумма", "REAL"),
                   ("СуммаНДС", "REAL")]}),
    "Document_ВыпускПродукции": ("document", [
        ("Подразделение_Key", "TEXT"), ("Склад_Key", "TEXT"), ("Смена", "TEXT")], {
        "Продукция": [("Номенклатура_Key", "TEXT"), ("Количество", "REAL"), ("КоличествоПлан", "REAL"),
                      ("КоличествоБрак", "REAL"), ("ТрудозатратыЧас", "REAL")]}),
    "Document_СписаниеНедостачТоваров": ("document", [
        ("Склад_Key", "TEXT"), ("ПричинаСписания", "TEXT")], {
        "Товары": [("Номенклатура_Key", "TEXT"), ("Количество", "REAL"), ("Стоимость", "REAL")]}),
    "Document_ОтражениеПрочихДоходовИРасходов": ("document", [("Комментарий", "TEXT")], {
        "ПрочиеРасходы": [("СтатьяРасходов_Key", "TEXT"), ("Подразделение_Key", "TEXT"), ("Сумма", "REAL"),
                          ("Содержание", "TEXT")]}),
    "Document_ПоступлениеБезналичныхДенежныхСредств": ("document", [
        ("Партнер_Key", "TEXT"), ("СуммаДокумента", "REAL")], {
        "РасшифровкаПлатежа": [("ОбъектРасчетов_Key", "TEXT"), ("Сумма", "REAL")]}),
}

REGISTER_RECORDS = {
    "AccumulationRegister_ВыручкаИСебестоимостьПродаж_RecordType": [
        ("Recorder", "TEXT"), ("Recorder_Type", "TEXT"), ("Period", "TEXT"), ("LineNumber", "INTEGER"),
        ("Active", "INTEGER"), ("Номенклатура_Key", "TEXT"), ("Партнер_Key", "TEXT"), ("Количество", "REAL"),
        ("СуммаВыручкиБезНДС", "REAL"), ("Стоимость", "REAL")],
}

# Stored balances behind virtual tables (…/Balance(Period=datetime'…')); not published as entity sets.
BALANCE_TABLES = {
    "_balance_ТоварыНаСкладах": [("Period", "TEXT"), ("Склад_Key", "TEXT"), ("Номенклатура_Key", "TEXT"),
                                 ("ВНаличииBalance", "REAL")],
    "InformationRegister_НормативыЗапасов": [("Склад_Key", "TEXT"), ("Номенклатура_Key", "TEXT"),
                                            ("МинимальныйЗапас", "REAL")],
}


def ref(obj: str, key: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{obj}:{key}"))


def dt(value: date | datetime | None) -> str:
    if value is None:
        return EMPTY_DATE
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="seconds")
    return f"{value.isoformat()}T00:00:00"


def money(value: Any) -> float:
    return round(float(value), 2)


def vat(net: float) -> float:
    return round(net * VAT_RATE, 2)


class Infobase:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("CREATE TABLE _meta_objects (name TEXT PRIMARY KEY, kind TEXT, sections TEXT)")
        self.db.execute("CREATE TABLE _infobase (key TEXT PRIMARY KEY, value TEXT)")
        for name, (kind, attrs, sections) in OBJECTS.items():
            header = HEADER_CATALOG if kind == "catalog" else HEADER_DOCUMENT
            self._create(name, header + attrs)
            for section, section_attrs in sections.items():
                self._create(f"{name}_{section}", LINE + section_attrs)
                self.db.execute(f'CREATE INDEX "ix_{name}_{section}" ON "{name}_{section}" (Ref_Key, LineNumber)')
            if kind == "document":
                self.db.execute(f'CREATE INDEX "ix_{name}_date" ON "{name}" (Date)')
            self.db.execute("INSERT INTO _meta_objects VALUES (?, ?, ?)", (name, kind, json.dumps(list(sections))))
        for name, columns in REGISTER_RECORDS.items():
            self._create(name, columns)
            self.db.execute(f'CREATE INDEX "ix_{name}_period" ON "{name}" (Period)')
            self.db.execute("INSERT INTO _meta_objects VALUES (?, 'register_records', '[]')", (name,))
        for name, columns in BALANCE_TABLES.items():
            self._create(name, columns)
        self.db.execute('CREATE INDEX "ix_balance_period" ON "_balance_ТоварыНаСкладах" (Period)')
        self.db.execute("INSERT INTO _meta_objects VALUES ('InformationRegister_НормативыЗапасов', 'information_register', '[]')")

    def _create(self, table: str, columns: list[tuple[str, str]]) -> None:
        cols = ", ".join(f'"{c}" {t}' for c, t in columns)
        self.db.execute(f'CREATE TABLE "{table}" ({cols})')

    def insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = list(rows[0])
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(f'"{c}"' for c in columns)
        self.db.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                            [tuple(r[c] for c in columns) for r in rows])

    def catalog(self, obj: str, key: Any, description: str, *, code: str | None = None, parent: str = EMPTY_REF,
                folder: bool = False, deleted: bool = False, **attrs: Any) -> dict[str, Any]:
        return {"Ref_Key": ref(obj, key), "DataVersion": "AAAAAQAAAAA=", "DeletionMark": int(deleted),
                "Code": code or str(key).zfill(9), "Description": description, "IsFolder": int(folder),
                "Parent_Key": parent, **attrs}

    @staticmethod
    def document(obj: str, key: Any, number: str, when: date, *, posted: bool = True, **attrs: Any) -> dict[str, Any]:
        return {"Ref_Key": ref(obj, key), "DataVersion": "AAAAAQAAAAA=", "DeletionMark": 0, "Number": number,
                "Date": dt(when), "Posted": int(posted), **attrs}

    def close(self) -> None:
        self.db.commit()
        self.db.execute("ANALYZE")
        self.db.commit()
        self.db.close()


def fetch(cur: Any, sql: str) -> list[tuple[Any, ...]]:
    cur.execute(sql)
    return cur.fetchall()


def export(conn: Any, target: Path) -> dict[str, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    ib = Infobase(tmp)
    counts: dict[str, int] = {}
    cur = conn.cursor()

    def put(table: str, rows: list[dict[str, Any]]) -> None:
        ib.insert(table, rows)
        counts[table] = counts.get(table, 0) + len(rows)

    # ------------------------------------------------------------------ catalogs
    kinds = {"Товар": "Готовая продукция", "Сырьё": "Сырьё", "Ингредиенты": "Ингредиенты", "Упаковка": "Упаковка"}
    kind_type = {"Товар": "Товар", "Сырьё": "Материал", "Ингредиенты": "Материал", "Упаковка": "Материал"}
    put("Catalog_ВидыНоменклатуры", [ib.catalog("Catalog_ВидыНоменклатуры", k, v, ТипНоменклатуры=kind_type[k])
                                     for k, v in kinds.items()])
    units = {"л": "л", "кг": "кг", "шт": "шт"}
    put("Catalog_УпаковкиЕдиницыИзмерения", [ib.catalog("Catalog_УпаковкиЕдиницыИзмерения", u, u) for u in units])

    categories = fetch(cur, "SELECT category_id, name FROM demo.product_categories ORDER BY 1")
    put("Catalog_ТоварныеКатегории", [ib.catalog("Catalog_ТоварныеКатегории", cid, name) for cid, name in categories])

    items = []
    for pid, sku, name, cid, unit, shelf, active in fetch(
            cur, "SELECT product_id, sku, name, category_id, package_unit, shelf_life_days, is_active FROM demo.products ORDER BY 1"):
        items.append(ib.catalog("Catalog_Номенклатура", f"p{pid}", name, code=f"ГП-{pid:05d}", deleted=not active,
                                Артикул=sku, ВидНоменклатуры_Key=ref("Catalog_ВидыНоменклатуры", "Товар"),
                                ТоварнаяКатегория_Key=ref("Catalog_ТоварныеКатегории", cid),
                                ЕдиницаИзмерения_Key=ref("Catalog_УпаковкиЕдиницыИзмерения", "шт"), СрокГодности=shelf))
    for mid, name, unit, mtype in fetch(cur, "SELECT material_id, name, unit, material_type FROM demo.raw_materials ORDER BY 1"):
        items.append(ib.catalog("Catalog_Номенклатура", f"m{mid}", name, code=f"МТ-{mid:05d}", Артикул=None,
                                ВидНоменклатуры_Key=ref("Catalog_ВидыНоменклатуры", mtype), ТоварнаяКатегория_Key=EMPTY_REF,
                                ЕдиницаИзмерения_Key=ref("Catalog_УпаковкиЕдиницыИзмерения", unit), СрокГодности=None))
    put("Catalog_Номенклатура", items)

    regions = fetch(cur, "SELECT region_id, name, macro_region FROM demo.regions ORDER BY 1")
    macro = sorted({r[2] for r in regions})
    put("Catalog_БизнесРегионы",
        [ib.catalog("Catalog_БизнесРегионы", f"g:{m}", m, folder=True) for m in macro]
        + [ib.catalog("Catalog_БизнесРегионы", rid, name, parent=ref("Catalog_БизнесРегионы", f"g:{m}"))
           for rid, name, m in regions])

    employees = fetch(cur, "SELECT employee_id, full_name, department, position, hire_date, termination_date, "
                           "monthly_salary FROM demo.employees ORDER BY 1")
    managers = {r[0] for r in fetch(cur, "SELECT DISTINCT sales_manager_id FROM demo.customers "
                                         "UNION SELECT DISTINCT sales_manager_id FROM demo.sales_orders")}
    put("Catalog_Пользователи", [ib.catalog("Catalog_Пользователи", eid, full_name,
                                            Сотрудник_Key=ref("Catalog_Сотрудники", eid))
                                 for eid, full_name, *_ in employees if eid in managers])

    lines = fetch(cur, "SELECT line_id, name, warehouse_id FROM demo.production_lines ORDER BY 1")
    expense_departments = [r[0] for r in fetch(cur, "SELECT DISTINCT department FROM demo.operating_expenses ORDER BY 1")]
    departments = sorted({e[2] for e in employees} | set(expense_departments))
    put("Catalog_СтруктураПредприятия",
        [ib.catalog("Catalog_СтруктураПредприятия", f"d:{d}", d, ПроизводственноеПодразделение=0) for d in departments]
        + [ib.catalog("Catalog_СтруктураПредприятия", "g:lines", "Производственные линии", folder=True,
                      ПроизводственноеПодразделение=None)]
        + [ib.catalog("Catalog_СтруктураПредприятия", f"l:{lid}", name, ПроизводственноеПодразделение=1,
                      parent=ref("Catalog_СтруктураПредприятия", "g:lines")) for lid, name, _ in lines])
    positions = sorted({e[3] for e in employees})
    put("Catalog_Должности", [ib.catalog("Catalog_Должности", p, p) for p in positions])
    put("Catalog_Сотрудники", [
        ib.catalog("Catalog_Сотрудники", eid, full_name, Подразделение_Key=ref("Catalog_СтруктураПредприятия", f"d:{dept}"),
                   Должность_Key=ref("Catalog_Должности", pos), ДатаПриема=dt(hired), ДатаУвольнения=dt(fired),
                   Оклад=money(salary))
        for eid, full_name, dept, pos, hired, fired, salary in employees])

    warehouses = fetch(cur, "SELECT warehouse_id, name, region_id, warehouse_type, capacity_pallets FROM demo.warehouses ORDER BY 1")
    wh_groups = {"plant": "Склады завода", "distribution_center": "Распределительные центры"}
    put("Catalog_Склады",
        [ib.catalog("Catalog_Склады", f"g:{k}", v, folder=True, БизнесРегион_Key=None, Вместимость=None)
         for k, v in wh_groups.items()]
        + [ib.catalog("Catalog_Склады", wid, name, parent=ref("Catalog_Склады", f"g:{wtype}"),
                      БизнесРегион_Key=ref("Catalog_БизнесРегионы", rid), Вместимость=cap)
           for wid, name, rid, wtype, cap in warehouses])

    customers = fetch(cur, "SELECT customer_id, name, customer_type, region_id, sales_manager_id, payment_terms_days, "
                           "credit_limit, created_at, is_active FROM demo.customers ORDER BY 1")
    suppliers = fetch(cur, "SELECT supplier_id, name, region_id, material_type, payment_terms_days FROM demo.suppliers ORDER BY 1")
    segments = sorted({c[2] for c in customers})
    supplier_groups = sorted({s[3] for s in suppliers})
    partners = [ib.catalog("Catalog_Партнеры", "g:clients", "Клиенты", folder=True)]
    partners += [ib.catalog("Catalog_Партнеры", f"g:c:{s}", s, folder=True, parent=ref("Catalog_Партнеры", "g:clients"))
                 for s in segments]
    partners += [ib.catalog("Catalog_Партнеры", "g:suppliers", "Поставщики", folder=True)]
    partners += [ib.catalog("Catalog_Партнеры", f"g:s:{s}", s, folder=True, parent=ref("Catalog_Партнеры", "g:suppliers"))
                 for s in supplier_groups]
    for folder in partners:
        folder.update(Клиент=None, Поставщик=None, БизнесРегион_Key=None, ОсновнойМенеджер_Key=None, БИН=None,
                      ДатаРегистрации=None, СрокОплатыДней=None, КредитныйЛимит=None)
    for cid, name, ctype, rid, manager, terms, limit, created, active in customers:
        partners.append(ib.catalog(
            "Catalog_Партнеры", f"c{cid}", name, code=f"КЛ-{cid:05d}", parent=ref("Catalog_Партнеры", f"g:c:{ctype}"),
            deleted=not active, Клиент=1, Поставщик=0, БизнесРегион_Key=ref("Catalog_БизнесРегионы", rid),
            ОсновнойМенеджер_Key=ref("Catalog_Пользователи", manager), БИН=f"{(cid * 7919 + 100000000000) % 10**12:012d}",
            ДатаРегистрации=dt(created), СрокОплатыДней=terms, КредитныйЛимит=money(limit)))
    for sid, name, rid, mtype, terms in suppliers:
        partners.append(ib.catalog(
            "Catalog_Партнеры", f"s{sid}", name, code=f"ПС-{sid:05d}", parent=ref("Catalog_Партнеры", f"g:s:{mtype}"),
            Клиент=0, Поставщик=1, БизнесРегион_Key=ref("Catalog_БизнесРегионы", rid), ОсновнойМенеджер_Key=EMPTY_REF,
            БИН=f"{(sid * 104729 + 900000000000) % 10**12:012d}", ДатаРегистрации=EMPTY_DATE, СрокОплатыДней=terms,
            КредитныйЛимит=0.0))
    put("Catalog_Партнеры", partners)

    expense_items = [r[0] for r in fetch(cur, "SELECT DISTINCT expense_category FROM demo.operating_expenses ORDER BY 1")]
    put("Catalog_СтатьиРасходов", [ib.catalog("Catalog_СтатьиРасходов", e, e) for e in expense_items])

    # ------------------------------------------------------------------ sales, costs of sales, payments
    sales_doc = "Document_РеализацияТоваровУслуг"
    orders = fetch(cur, "SELECT order_id, order_number, customer_id, warehouse_id, sales_manager_id, order_date, status, "
                        "payment_due_date, paid_date FROM demo.sales_orders ORDER BY order_id")
    lines_by_order: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    for row in fetch(cur, "SELECT order_id, order_item_id, product_id, quantity, unit_price, discount_pct, revenue_amount, "
                          "cost_amount FROM demo.sales_order_items ORDER BY order_id, order_item_id"):
        lines_by_order[row[0]].append(row)
    headers, tab, records, pay_headers, pay_lines = [], [], [], [], []
    order_info: dict[int, tuple[str, str, str, str]] = {}
    for oid, number, cid, wid, manager, when, status, due, paid in orders:
        doc_ref = ref(sales_doc, oid)
        posted = status != "cancelled"
        total = 0.0
        for n, (_, _, pid, qty, price, disc, revenue, cost) in enumerate(lines_by_order[oid], 1):
            net = money(revenue)
            line_vat = vat(net)
            total += net + line_vat
            tab.append({"Ref_Key": doc_ref, "LineNumber": n, "Номенклатура_Key": ref("Catalog_Номенклатура", f"p{pid}"),
                        "Количество": float(qty), "Цена": money(float(price) * (1 + VAT_RATE)),
                        "ПроцентРучнойСкидки": float(disc), "СтавкаНДС": "12%", "Сумма": money(net + line_vat),
                        "СуммаНДС": line_vat})
            if posted:
                records.append({"Recorder": doc_ref, "Recorder_Type": f"StandardODATA.{sales_doc}", "Period": dt(when),
                                "LineNumber": n, "Active": 1, "Номенклатура_Key": ref("Catalog_Номенклатура", f"p{pid}"),
                                "Партнер_Key": ref("Catalog_Партнеры", f"c{cid}"), "Количество": float(qty),
                                "СуммаВыручкиБезНДС": net, "Стоимость": money(cost)})
        headers.append(ib.document(sales_doc, oid, number, when, posted=posted,
                                   Партнер_Key=ref("Catalog_Партнеры", f"c{cid}"), Склад_Key=ref("Catalog_Склады", wid),
                                   Менеджер_Key=ref("Catalog_Пользователи", manager), ДатаПлатежа=dt(due),
                                   СуммаДокумента=money(total), Комментарий=None))
        order_info[oid] = (doc_ref, ref("Catalog_Партнеры", f"c{cid}"), ref("Catalog_Склады", wid),
                           ref("Catalog_Пользователи", manager))
        if posted and paid is not None:
            pay_ref = ref("Document_ПоступлениеБезналичныхДенежныхСредств", oid)
            pay_headers.append(ib.document("Document_ПоступлениеБезналичныхДенежныхСредств", oid,
                                           f"ПБ-{paid.year}-{oid:06d}", paid, Партнер_Key=ref("Catalog_Партнеры", f"c{cid}"),
                                           СуммаДокумента=money(total)))
            pay_lines.append({"Ref_Key": pay_ref, "LineNumber": 1, "ОбъектРасчетов_Key": doc_ref, "Сумма": money(total)})
    put(sales_doc, headers)
    put(f"{sales_doc}_Товары", tab)
    put("AccumulationRegister_ВыручкаИСебестоимостьПродаж_RecordType", records)
    put("Document_ПоступлениеБезналичныхДенежныхСредств", pay_headers)
    put("Document_ПоступлениеБезналичныхДенежныхСредств_РасшифровкаПлатежа", pay_lines)
    del headers, tab, records, lines_by_order

    ret_doc = "Document_ВозвратТоваровОтКлиента"
    ret_headers, ret_lines = [], []
    for rid, oid, pid, when, qty, reason, refund in fetch(
            cur, "SELECT return_id, order_id, product_id, return_date, quantity, reason, refund_amount "
                 "FROM demo.sales_returns ORDER BY return_id"):
        doc_ref, partner, warehouse, manager = order_info[oid]
        net = money(refund)
        ret_headers.append(ib.document(ret_doc, rid, f"ВК-{when.year}-{rid:05d}", when, Партнер_Key=partner,
                                       Склад_Key=warehouse, Менеджер_Key=manager, ДокументРеализации_Key=doc_ref,
                                       СуммаДокумента=money(net + vat(net))))
        ret_lines.append({"Ref_Key": ref(ret_doc, rid), "LineNumber": 1, "Номенклатура_Key": ref("Catalog_Номенклатура", f"p{pid}"),
                          "Количество": float(qty), "Сумма": money(net + vat(net)), "СуммаНДС": vat(net),
                          "ПричинаВозврата": reason})
    put(ret_doc, ret_headers)
    put(f"{ret_doc}_Товары", ret_lines)

    # ------------------------------------------------------------------ purchases
    po_doc = "Document_ПоступлениеТоваровУслуг"
    po_lines: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    for row in fetch(cur, "SELECT purchase_order_id, material_id, quantity, unit_price, amount FROM demo.purchase_order_items "
                          "ORDER BY purchase_order_id, purchase_order_item_id"):
        po_lines[row[0]].append(row)
    po_headers, po_tab = [], []
    for poid, number, sid, wid, when, delivered, status in fetch(
            cur, "SELECT purchase_order_id, po_number, supplier_id, warehouse_id, order_date, delivered_date, status "
                 "FROM demo.purchase_orders ORDER BY 1"):
        total = 0.0
        for n, (_, mid, qty, price, amount) in enumerate(po_lines[poid], 1):
            net = money(amount)
            total += net + vat(net)
            po_tab.append({"Ref_Key": ref(po_doc, poid), "LineNumber": n, "Номенклатура_Key": ref("Catalog_Номенклатура", f"m{mid}"),
                           "Количество": float(qty), "Цена": money(float(price) * (1 + VAT_RATE)),
                           "Сумма": money(net + vat(net)), "СуммаНДС": vat(net)})
        po_headers.append(ib.document(po_doc, poid, number, when, posted=status != "cancelled",
                                      Партнер_Key=ref("Catalog_Партнеры", f"s{sid}"), Склад_Key=ref("Catalog_Склады", wid),
                                      ДатаПоступления=dt(delivered), СуммаДокумента=money(total)))
    put(po_doc, po_headers)
    put(f"{po_doc}_Товары", po_tab)

    # ------------------------------------------------------------------ production
    line_wh = {lid: wid for lid, _, wid in lines}
    shifts = {"day": "Дневная", "night": "Ночная"}
    out_headers, out_tab = [], []
    for bid, lid, pid, when, shift, planned, produced, defect, hours in fetch(
            cur, "SELECT batch_id, line_id, product_id, production_date, shift, planned_quantity, produced_quantity, "
                 "defect_quantity, labor_hours FROM demo.production_batches ORDER BY 1"):
        out_headers.append(ib.document("Document_ВыпускПродукции", bid, f"ВП-{when.year}-{bid:06d}", when,
                                       Подразделение_Key=ref("Catalog_СтруктураПредприятия", f"l:{lid}"),
                                       Склад_Key=ref("Catalog_Склады", line_wh[lid]), Смена=shifts[shift]))
        out_tab.append({"Ref_Key": ref("Document_ВыпускПродукции", bid), "LineNumber": 1,
                        "Номенклатура_Key": ref("Catalog_Номенклатура", f"p{pid}"), "Количество": float(produced),
                        "КоличествоПлан": float(planned), "КоличествоБрак": float(defect), "ТрудозатратыЧас": float(hours)})
    put("Document_ВыпускПродукции", out_headers)
    put("Document_ВыпускПродукции_Продукция", out_tab)

    # ------------------------------------------------------------------ write-offs and other expenses
    wo_headers, wo_tab = [], []
    for wid_, when, wid, pid, qty, reason, cost in fetch(
            cur, "SELECT write_off_id, write_off_date, warehouse_id, product_id, quantity, reason, cost_amount "
                 "FROM demo.write_offs ORDER BY 1"):
        wo_headers.append(ib.document("Document_СписаниеНедостачТоваров", wid_, f"СН-{when.year}-{wid_:06d}", when,
                                      Склад_Key=ref("Catalog_Склады", wid), ПричинаСписания=reason))
        wo_tab.append({"Ref_Key": ref("Document_СписаниеНедостачТоваров", wid_), "LineNumber": 1,
                       "Номенклатура_Key": ref("Catalog_Номенклатура", f"p{pid}"), "Количество": float(qty),
                       "Стоимость": money(cost)})
    put("Document_СписаниеНедостачТоваров", wo_headers)
    put("Document_СписаниеНедостачТоваров_Товары", wo_tab)

    ex_doc = "Document_ОтражениеПрочихДоходовИРасходов"
    ex_headers, ex_tab = [], []
    for eid, when, category, dept, amount, description in fetch(
            cur, "SELECT expense_id, expense_date, expense_category, department, amount, description "
                 "FROM demo.operating_expenses ORDER BY 1"):
        ex_headers.append(ib.document(ex_doc, eid, f"ПР-{when.year}-{eid:05d}", when, Комментарий=None))
        ex_tab.append({"Ref_Key": ref(ex_doc, eid), "LineNumber": 1, "СтатьяРасходов_Key": ref("Catalog_СтатьиРасходов", category),
                       "Подразделение_Key": ref("Catalog_СтруктураПредприятия", f"d:{dept}"), "Сумма": money(amount),
                       "Содержание": description})
    put(ex_doc, ex_headers)
    put(f"{ex_doc}_ПрочиеРасходы", ex_tab)

    # ------------------------------------------------------------------ stock balances and replenishment norms
    balances = []
    norms: dict[tuple[int, int], float] = {}
    for when, wid, pid, qty, reorder in fetch(
            cur, "SELECT snapshot_date, warehouse_id, product_id, quantity_on_hand, reorder_point "
                 "FROM demo.inventory_snapshots ORDER BY snapshot_date, warehouse_id, product_id"):
        balances.append({"Period": dt(when), "Склад_Key": ref("Catalog_Склады", wid),
                         "Номенклатура_Key": ref("Catalog_Номенклатура", f"p{pid}"), "ВНаличииBalance": float(qty)})
        norms[(wid, pid)] = float(reorder)
    put("_balance_ТоварыНаСкладах", balances)
    put("InformationRegister_НормативыЗапасов", [
        {"Склад_Key": ref("Catalog_Склады", wid), "Номенклатура_Key": ref("Catalog_Номенклатура", f"p{pid}"),
         "МинимальныйЗапас": value} for (wid, pid), value in sorted(norms.items())])

    end = fetch(cur, "SELECT max(order_date) FROM demo.sales_orders")[0][0]
    ib.db.executemany("INSERT INTO _infobase VALUES (?, ?)", [
        ("configuration", CONFIGURATION), ("generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("data_end", end.isoformat() if end else ""), ("vat_rate", str(VAT_RATE))])
    ib.close()
    os.replace(tmp, target)
    return counts


def main() -> int:
    import psycopg

    url = os.environ.get("SEED_DATABASE_URL")
    target = Path(os.environ.get("ONEC_INFOBASE_PATH") or "/infobase/demo_1c.sqlite")
    if not url:
        print("SEED_DATABASE_URL is not set", file=sys.stderr)
        return 2
    started = time.monotonic()
    with psycopg.connect(url) as conn:
        counts = export(conn, target)
    print(f"1C infobase exported to {target} in {time.monotonic() - started:.1f}s: "
          + ", ".join(f"{k}={v}" for k, v in counts.items() if not k.startswith("Catalog_")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
