"""BizNext Canonical Data Contract: loading, type coercion and validation of records before sending."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

EMPTY_REF = "00000000-0000-0000-0000-000000000000"
EMPTY_DATE_PREFIX = "0001-01-01"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    required: bool = False
    enum: tuple[str, ...] = ()
    sensitive: bool = False


@dataclass(frozen=True)
class EntitySpec:
    name: str
    kind: str  # catalog | document | snapshot
    load: str  # replace_all | replace_window
    key: tuple[str, ...]
    fields: dict[str, FieldSpec]
    window_field: str | None = None
    optional: bool = False
    label: dict[str, str] = field(default_factory=dict)


class Contract:
    def __init__(self, version: str, entities: dict[str, EntitySpec]) -> None:
        self.version = version
        self.entities = entities

    @classmethod
    def load(cls, path: str | Path) -> "Contract":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        entities = {}
        for name, spec in raw["entities"].items():
            fields = {fname: FieldSpec(fname, f["type"], bool(f.get("required")), tuple(f.get("enum") or ()),
                                       bool(f.get("sensitive"))) for fname, f in spec["fields"].items()}
            entities[name] = EntitySpec(name, spec["kind"], spec["load"], tuple(spec["key"]), fields,
                                        spec.get("window_field"), bool(spec.get("optional")), spec.get("label") or {})
        return cls(str(raw["version"]), entities)

    def entity(self, name: str) -> EntitySpec:
        try:
            return self.entities[name]
        except KeyError as exc:
            raise KeyError(f"entity '{name}' is not part of contract v{self.version}") from exc

    def coerce(self, entity: str, record: dict[str, Any]) -> dict[str, Any]:
        spec = self.entity(entity)
        return {name: coerce_value(spec.fields[name].type, value) for name, value in record.items() if name in spec.fields}

    def problems(self, entity: str, record: dict[str, Any]) -> list[str]:
        spec = self.entity(entity)
        issues = [f"unknown field '{name}'" for name in record if name not in spec.fields]
        for name, f in spec.fields.items():
            value = record.get(name)
            if value is None:
                if f.required:
                    issues.append(f"'{name}' is required")
                continue
            if f.enum and value not in f.enum:
                issues.append(f"'{name}' must be one of {list(f.enum)}, got {value!r}")
        return issues


def coerce_value(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind == "string":
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip()
        return None if text in ("", EMPTY_REF) else text
    if kind == "date":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value).strip()
        if not text or text.startswith(EMPTY_DATE_PREFIX):
            return None
        return date.fromisoformat(text[:10]).isoformat()
    if kind == "decimal":
        if value == "":
            return None
        return round(float(value), 4)
    if kind == "integer":
        return None if value == "" else int(value)
    if kind == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    raise ValueError(f"unsupported contract type {kind}")
