"""1C:Enterprise OData emulator — demo mode of the BizNext 1C Connector (§56).

Plays the enterprise's 1C web publication: `/<infobase>/odata/standard.odata/…` over a 1C-shaped
infobase snapshot (SQLite, produced by database/seed/onec_export.py). It lives only in the
"enterprise" Docker network: the BizNext backend and agent cannot reach it; only the BizNext
Connector can, exactly as with a real on-premise 1C.

The published user is read-only: any write method returns 403, like a 1C role without "Изменение".
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from odata_filter import FilterError, to_sql

INFOBASE_PATH = Path(os.environ.get("ONEC_INFOBASE_PATH") or "/infobase/demo_1c.sqlite")
INFOBASE_NAME = os.environ.get("ONEC_INFOBASE_NAME") or "demo_foods"
USER = os.environ.get("ONEC_DEMO_USER") or "biznext_reader"
PASSWORD = os.environ.get("ONEC_DEMO_PASSWORD") or ""
MAX_TOP = 10000
EMPTY_REF = "00000000-0000-0000-0000-000000000000"
BOOLEAN_COLUMNS = {"DeletionMark", "Posted", "IsFolder", "Active", "Клиент", "Поставщик", "ПроизводственноеПодразделение"}
PREFIX = f"/{INFOBASE_NAME}/odata/standard.odata"

app = FastAPI(title="1C OData emulator (BizNext demo)", docs_url=None, redoc_url=None, openapi_url=None)


def odata_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"odata.error": {"code": code, "message": {"lang": "ru", "value": message}}},
                        headers={"WWW-Authenticate": 'Basic realm="1C:Enterprise"'} if status == 401 else None)


def connect() -> sqlite3.Connection:
    if not INFOBASE_PATH.is_file():
        raise FileNotFoundError(str(INFOBASE_PATH))
    conn = sqlite3.connect(f"file:{INFOBASE_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def objects(conn: sqlite3.Connection) -> dict[str, tuple[str, list[str]]]:
    return {r["name"]: (r["kind"], json.loads(r["sections"])) for r in conn.execute("SELECT * FROM _meta_objects")}


def columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {r["name"]: r["type"] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def jsonify(row: sqlite3.Row, types: dict[str, str], wanted: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in (row.keys() if wanted is None else wanted):
        value = row[name]
        if name in BOOLEAN_COLUMNS:
            value = bool(value)
        elif name == "LineNumber":
            value = str(value)
        elif value is None:
            value = EMPTY_REF if name.endswith("_Key") else (0 if types.get(name) in ("REAL", "INTEGER") else "")
        out[name] = value
    return out


@app.middleware("http")
async def basic_auth(request: Request, call_next: Any) -> Response:
    if request.url.path == "/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    ok = False
    if header.lower().startswith("basic ") and PASSWORD:
        try:
            user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            ok = secrets.compare_digest(user, USER) and secrets.compare_digest(password, PASSWORD)
        except (ValueError, UnicodeDecodeError):
            ok = False
    if not ok:
        return odata_error(401, "401", "Не выполнена аутентификация")
    if request.method not in ("GET", "HEAD"):
        return odata_error(403, "403", "Недостаточно прав на изменение данных. Пользователь публикации имеет роль «Только чтение».")
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok" if INFOBASE_PATH.is_file() else "no_infobase"}


@app.get(PREFIX + "/")
@app.get(PREFIX)
async def service_document() -> Response:
    try:
        with closing(connect()) as conn:
            names = sorted(objects(conn))
            info = dict(conn.execute("SELECT key, value FROM _infobase").fetchall())
    except FileNotFoundError:
        return odata_error(503, "503", "Информационная база недоступна")
    names += ["AccumulationRegister_ТоварыНаСкладах", "AccumulationRegister_РасчетыСКлиентамиПоСрокам"]
    return JSONResponse({"odata.metadata": f"{PREFIX}/$metadata", "configuration": info.get("configuration"),
                         "value": [{"name": n, "url": n} for n in sorted(names)]})


@app.get(PREFIX + "/$metadata")
async def metadata() -> Response:
    with closing(connect()) as conn:
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="1.0">',
                 '<edmx:DataServices><Schema Namespace="StandardODATA">']
        for name, (_, sections) in sorted(objects(conn).items()):
            parts.append(f'<EntityType Name="{name}">')
            parts += [f'<Property Name="{c}" Type="{_edm(t, c)}"/>' for c, t in columns(conn, name).items()]
            parts += [f'<Property Name="{s}" Type="Collection(StandardODATA.{name}_{s}_RowType)"/>' for s in sections]
            parts.append("</EntityType>")
        parts.append("</Schema></edmx:DataServices></edmx:Edmx>")
    return Response("\n".join(parts), media_type="application/xml")


def _edm(sqlite_type: str, name: str) -> str:
    if name in BOOLEAN_COLUMNS:
        return "Edm.Boolean"
    if name.endswith("_Key") or name in ("Ref_Key", "Recorder"):
        return "Edm.Guid"
    return {"REAL": "Edm.Double", "INTEGER": "Edm.Int64"}.get(sqlite_type, "Edm.String")


@app.get(PREFIX + "/{resource:path}")
async def resource(resource: str, request: Request) -> Response:
    resource = unquote(resource)
    params = request.query_params
    try:
        with closing(connect()) as conn:
            if resource.endswith(")") and "/Balance(" in resource:
                return balance(conn, resource)
            count_only = resource.endswith("/$count")
            name = resource.removesuffix("/$count")
            registry = objects(conn)
            if name not in registry:
                return odata_error(404, "14", f"Не найден объект «{name}» в составе стандартного интерфейса OData")
            return entity_set(conn, name, registry[name], params, count_only)
    except FileNotFoundError:
        return odata_error(503, "503", "Информационная база недоступна")
    except FilterError as exc:
        return odata_error(400, "6", f"Ошибка разбора $filter: {exc}")


def entity_set(conn: sqlite3.Connection, name: str, meta: tuple[str, list[str]], params: Any, count_only: bool) -> Response:
    kind, sections = meta
    types = columns(conn, name)
    where, args = "", []
    if params.get("$filter"):
        clause, args = to_sql(params["$filter"], set(types))
        where = f" WHERE {clause}"
    if count_only:
        return PlainTextResponse(str(conn.execute(f'SELECT count(*) FROM "{name}"{where}', args).fetchone()[0]))

    header_select, section_select = _select(params.get("$select"), list(types), sections)
    order_parts = []
    for item in (params.get("$orderby") or "").split(","):
        if not item.strip():
            continue
        field, _, direction = item.strip().partition(" ")
        if field not in types:
            return odata_error(400, "6", f"Неизвестное поле сортировки «{field}»")
        order_parts.append(f'"{field}" {"DESC" if direction.strip().lower() == "desc" else "ASC"}')
    order = f" ORDER BY {', '.join(order_parts)}" if order_parts else ""
    try:
        top = min(int(params.get("$top") or MAX_TOP), MAX_TOP)
        skip = max(int(params.get("$skip") or 0), 0)
    except ValueError:
        return odata_error(400, "6", "$top и $skip должны быть целыми числами")

    rows = conn.execute(f'SELECT * FROM "{name}"{where}{order} LIMIT ? OFFSET ?', [*args, top, skip]).fetchall()
    items = [jsonify(r, types, header_select) for r in rows]
    if sections and section_select and rows:
        refs = [r["Ref_Key"] for r in rows]
        for section, fields in section_select.items():
            table = f"{name}_{section}"
            section_types = columns(conn, table)
            wanted = ["Ref_Key", "LineNumber", *[f for f in (fields or section_types) if f not in ("Ref_Key", "LineNumber")]]
            by_ref: dict[str, list[dict[str, Any]]] = {}
            placeholders = ", ".join("?" for _ in refs)
            for line in conn.execute(f'SELECT * FROM "{table}" WHERE Ref_Key IN ({placeholders}) ORDER BY Ref_Key, LineNumber', refs):
                by_ref.setdefault(line["Ref_Key"], []).append(jsonify(line, section_types, wanted))
            for item, row in zip(items, rows, strict=True):
                item[section] = by_ref.get(row["Ref_Key"], [])
    body: dict[str, Any] = {"odata.metadata": f"{PREFIX}/$metadata#{name}", "value": items}
    if params.get("$inlinecount") == "allpages":
        body["odata.count"] = str(conn.execute(f'SELECT count(*) FROM "{name}"{where}', args).fetchone()[0])
    return JSONResponse(body)


def _select(raw: str | None, header: list[str], sections: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    if not raw:
        return header, {s: [] for s in sections}
    header_fields: list[str] = []
    section_fields: dict[str, list[str]] = {}
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        section, _, field = item.partition("/")
        if section in sections:
            section_fields.setdefault(section, [])
            if field:
                section_fields[section].append(field)
        elif item in header:
            header_fields.append(item)
        else:
            raise FilterError(f"unknown field in $select: {item}")
    return header_fields, section_fields


def balance(conn: sqlite3.Connection, resource: str) -> Response:
    register, _, args = resource.partition("/Balance(")
    period = None
    for part in args.rstrip(")").split(","):
        key, _, value = part.partition("=")
        if key.strip() == "Period" and value.startswith("datetime'"):
            period = value[len("datetime'"):-1]
    if not period:
        return odata_error(400, "6", "Для виртуальной таблицы остатков укажите Period=datetime'…'")
    if register == "AccumulationRegister_ТоварыНаСкладах":
        rows = conn.execute(
            'SELECT "Склад_Key", "Номенклатура_Key", "ВНаличииBalance" FROM "_balance_ТоварыНаСкладах" '
            'WHERE Period = (SELECT max(Period) FROM "_balance_ТоварыНаСкладах" WHERE Period <= ?) AND "ВНаличииBalance" <> 0 '
            'ORDER BY "Склад_Key", "Номенклатура_Key"', [period]).fetchall()
    elif register == "AccumulationRegister_РасчетыСКлиентамиПоСрокам":
        rows = conn.execute(
            """
            SELECT r."Партнер_Key", r.Ref_Key AS "ОбъектРасчетов_Key", r."ДатаПлатежа" AS "ДатаПлановогоПогашения",
                   round(r."СуммаДокумента" - COALESCE(p.paid, 0), 2) AS "СуммаBalance"
            FROM "Document_РеализацияТоваровУслуг" r
            LEFT JOIN (SELECT t."ОбъектРасчетов_Key" AS doc, sum(t."Сумма") AS paid
                       FROM "Document_ПоступлениеБезналичныхДенежныхСредств_РасшифровкаПлатежа" t
                       JOIN "Document_ПоступлениеБезналичныхДенежныхСредств" h ON h.Ref_Key = t.Ref_Key
                       WHERE h.Posted = 1 AND h.DeletionMark = 0 AND h.Date <= ? GROUP BY t."ОбъектРасчетов_Key") p
              ON p.doc = r.Ref_Key
            WHERE r.Posted = 1 AND r.DeletionMark = 0 AND r.Date <= ? AND r."СуммаДокумента" - COALESCE(p.paid, 0) > 0.005
            ORDER BY r.Ref_Key
            """, [period, period]).fetchall()
    else:
        return odata_error(404, "14", f"Виртуальная таблица остатков для «{register}» не опубликована")
    items = [dict(r) for r in rows]
    return JSONResponse({"odata.metadata": f"{PREFIX}/$metadata#{register}_Balance", "value": items})
