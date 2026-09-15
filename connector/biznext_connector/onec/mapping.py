"""Declarative 1C → canonical mapping (connector/mappings/*.yaml): specification and expression evaluation."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from biznext_connector.adapters import SnapshotPolicy
from biznext_connector.contract import EMPTY_REF

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_EXPR_KEYS = {"const", "lookup", "coalesce", "not", "expr", "format", "enriched", "path"}


class MappingError(ValueError):
    pass


@dataclass(frozen=True)
class EnrichSpec:
    name: str
    object: str
    date: str
    match: dict[str, str]  # register field -> path in the document context
    sum: str
    filter: str | None = None
    order_by: str | None = None
    allocate_register: str | None = None
    allocate_line: str | None = None


@dataclass(frozen=True)
class CompleteSpec:
    object: str
    key: tuple[str, ...]
    defaults: dict[str, Any]


@dataclass
class SourceSpec:
    entity: str
    fields: dict[str, Any]
    object: str | None = None
    balance: str | None = None
    filter: str | None = None
    date: str | None = None
    lines: str | None = None
    enrich: list[EnrichSpec] = field(default_factory=list)
    snapshots: SnapshotPolicy | None = None
    complete_from: CompleteSpec | None = None

    @property
    def main_object(self) -> str:
        return self.object or self.balance or ""

    def referenced_objects(self) -> list[str]:
        found = [self.main_object]
        for expr in self.fields.values():
            found += [obj for obj, _, _ in lookups_of(expr)]
        found += [e.object for e in self.enrich]
        if self.complete_from:
            found.append(self.complete_from.object)
        return list(dict.fromkeys(found))


@dataclass
class Mapping:
    version: int
    configuration: str
    entities: dict[str, list[SourceSpec]]

    @classmethod
    def load(cls, path: str | Path) -> "Mapping":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        entities: dict[str, list[SourceSpec]] = {}
        for entity, items in (raw.get("entities") or {}).items():
            specs = items if isinstance(items, list) else [items]
            entities[entity] = [_source_spec(entity, item) for item in specs]
        return cls(int(raw.get("version") or 1), str(raw.get("configuration") or ""), entities)


def _source_spec(entity: str, raw: dict[str, Any]) -> SourceSpec:
    if bool(raw.get("object")) == bool(raw.get("balance")):
        raise MappingError(f"{entity}: exactly one of 'object' or 'balance' is required")
    if raw.get("object") and raw.get("lines") and not raw.get("date"):
        raise MappingError(f"{entity}: documents with lines need 'date'")
    enrich = []
    for name, e in (raw.get("enrich") or {}).items():
        allocate = e.get("allocate_by") or {}
        enrich.append(EnrichSpec(name, e["object"], e["date"], dict(e["match"]), e["sum"], e.get("filter"),
                                 e.get("order_by"), allocate.get("register"), allocate.get("line")))
    snapshots = None
    if raw.get("snapshots"):
        snapshots = SnapshotPolicy(raw["snapshots"].get("history", "month_end"), int(raw["snapshots"].get("recent_days", 1)))
    complete = None
    if raw.get("complete_from"):
        c = raw["complete_from"]
        complete = CompleteSpec(c["object"], tuple(c["key"]), dict(c.get("defaults") or {}))
    fields = dict(raw.get("fields") or {})
    for name, expr in fields.items():
        _validate(expr, f"{entity}.{name}")
    return SourceSpec(entity, fields, raw.get("object"), raw.get("balance"), raw.get("filter"), raw.get("date"),
                      raw.get("lines"), enrich, snapshots, complete)


def _validate(expr: Any, where: str) -> None:
    if isinstance(expr, str) or isinstance(expr, (int, float, bool)) or expr is None:
        return
    if not isinstance(expr, dict):
        raise MappingError(f"{where}: unsupported expression {expr!r}")
    ops = _EXPR_KEYS & set(expr)
    if len(ops) != 1:
        raise MappingError(f"{where}: expression needs exactly one of {sorted(_EXPR_KEYS)}")
    if "expr" in expr:
        _parse_expr(expr["expr"])
    if "coalesce" in expr:
        for item in expr["coalesce"]:
            _validate(item, where)
    if "lookup" in expr and ("attr" not in expr or "key" not in expr):
        raise MappingError(f"{where}: lookup needs 'key' and 'attr'")


# ---------------------------------------------------------------------- lookups


def normalize_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return None if text in ("", EMPTY_REF) else text


class Lookups:
    """In-memory indexes of catalogs and information registers used by lookup expressions."""

    def __init__(self) -> None:
        self.tables: dict[tuple[str, tuple[str, ...]], dict[tuple[str | None, ...], dict[str, Any]]] = {}

    def add(self, obj: str, key_fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
        index = self.tables.setdefault((obj, key_fields), {})
        for row in rows:
            index[tuple(normalize_key(row.get(k)) for k in key_fields)] = row

    def get(self, obj: str, key_fields: tuple[str, ...], values: tuple[Any, ...], attr: str) -> Any:
        key = tuple(normalize_key(v) for v in values)
        if any(k is None for k in key):
            return None
        row = self.tables.get((obj, key_fields), {}).get(key)
        return None if row is None else row.get(attr)

    def keys(self, obj: str, key_fields: tuple[str, ...]) -> list[tuple[str | None, ...]]:
        return list(self.tables.get((obj, key_fields), {}))


@dataclass
class RowContext:
    header: dict[str, Any]
    line: dict[str, Any] | None
    lookups: Lookups
    enriched: dict[str, Any] = field(default_factory=dict)

    def path(self, path: str) -> Any:
        if path.startswith("line."):
            return (self.line or {}).get(path[5:])
        return self.header.get(path)


# ---------------------------------------------------------------------- expressions


def _key_fields(key: Any) -> tuple[str, ...]:
    if isinstance(key, list):
        return tuple(k.removeprefix("line.") for k in key)
    return ("Ref_Key",)


def lookups_of(expr: Any) -> list[tuple[str, tuple[str, ...], str]]:
    """(object, key fields in that object, attribute) for every lookup inside an expression."""
    if not isinstance(expr, dict):
        return []
    found: list[tuple[str, tuple[str, ...], str]] = []
    if "lookup" in expr:
        found.append((expr["lookup"], _key_fields(expr["key"]), expr["attr"]))
        then = expr.get("then")
        while isinstance(then, dict) and "lookup" in then:
            found.append((then["lookup"], ("Ref_Key",), then["attr"]))
            then = then.get("then")
    for item in expr.get("coalesce") or []:
        found += lookups_of(item)
    return found


def paths_of(expr: Any) -> set[str]:
    """Header / line paths an expression reads from the current row (for $select)."""
    if isinstance(expr, str):
        return {expr}
    if not isinstance(expr, dict):
        return set()
    if "path" in expr:
        return {expr["path"]}
    if "lookup" in expr:
        key = expr["key"]
        return set(key) if isinstance(key, list) else {key}
    if "not" in expr:
        return {expr["not"]}
    if "coalesce" in expr:
        return set().union(*(paths_of(item) for item in expr["coalesce"]))
    if "expr" in expr:
        return _expr_names(_parse_expr(expr["expr"]))
    if "format" in expr:
        return set(_PLACEHOLDER.findall(expr["format"]))
    return set()


def evaluate(expr: Any, ctx: RowContext) -> Any:
    if isinstance(expr, dict) and "map" in expr:
        value = _evaluate({k: v for k, v in expr.items() if k != "map"}, ctx)
        return expr["map"].get(value, value) if value is not None else None
    return _evaluate(expr, ctx)


def _evaluate(expr: Any, ctx: RowContext) -> Any:
    if isinstance(expr, str):
        return ctx.path(expr)
    if not isinstance(expr, dict):
        return expr
    if "const" in expr:
        return expr["const"]
    if "path" in expr:
        return ctx.path(expr["path"])
    if "lookup" in expr:
        key = expr["key"]
        values = tuple(ctx.path(k) for k in key) if isinstance(key, list) else (ctx.path(key),)
        value = ctx.lookups.get(expr["lookup"], _key_fields(key), values, expr["attr"])
        then = expr.get("then")
        while isinstance(then, dict) and value is not None:
            value = ctx.lookups.get(then["lookup"], ("Ref_Key",), (value,), then["attr"])
            then = then.get("then")
        return value
    if "coalesce" in expr:
        for item in expr["coalesce"]:
            value = evaluate(item, ctx)
            if value not in (None, "", EMPTY_REF):
                return value
        return None
    if "not" in expr:
        value = ctx.path(expr["not"])
        return None if value is None else not bool(value)
    if "expr" in expr:
        return _arith(_parse_expr(expr["expr"]), ctx)
    if "format" in expr:
        return _PLACEHOLDER.sub(lambda m: "" if ctx.path(m.group(1)) is None else str(ctx.path(m.group(1))), expr["format"])
    if "enriched" in expr:
        return ctx.enriched.get(expr["enriched"])
    raise MappingError(f"unsupported expression {expr!r}")


_PARSED: dict[str, ast.expr] = {}


def _parse_expr(text: str) -> ast.expr:
    if text in _PARSED:
        return _PARSED[text]
    try:
        body = ast.parse(text, mode="eval").body
    except SyntaxError as exc:
        raise MappingError(f"invalid expression {text!r}") from exc
    for node in ast.walk(body):
        allowed = (ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Name,
                   ast.Attribute, ast.Load)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            continue
        if not isinstance(node, allowed):
            raise MappingError(f"expression {text!r} contains a forbidden element {type(node).__name__}")
        if isinstance(node, ast.Attribute) and not (isinstance(node.value, ast.Name) and node.value.id == "line"):
            raise MappingError(f"expression {text!r}: only line.<field> attributes are allowed")
    _PARSED[text] = body
    return body


def _expr_names(node: ast.expr) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            names.add(f"line.{child.attr}")
    attribute_bases = {id(c.value) for c in ast.walk(node) if isinstance(c, ast.Attribute)}
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and id(child) not in attribute_bases:
            names.add(child.id)
    return names


def _arith(node: ast.expr, ctx: RowContext) -> float | None:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        value = ctx.path(node.id)
        return None if value in (None, "") else float(value)
    if isinstance(node, ast.Attribute):
        value = ctx.path(f"line.{node.attr}")
        return None if value in (None, "") else float(value)
    if isinstance(node, ast.UnaryOp):
        operand = _arith(node.operand, ctx)
        return None if operand is None else (-operand if isinstance(node.op, ast.USub) else operand)
    if isinstance(node, ast.BinOp):
        left, right = _arith(node.left, ctx), _arith(node.right, ctx)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return round(left + right, 4)
        if isinstance(node.op, ast.Sub):
            return round(left - right, 4)
        if isinstance(node.op, ast.Mult):
            return round(left * right, 4)
        return None if right == 0 else round(left / right, 4)
    raise MappingError("unsupported expression element")
