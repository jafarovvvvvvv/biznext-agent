"""Server-side view of the canonical data contract (contracts/canonical/v1.yaml) with strict record validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_STRING = 2000


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class FieldDef:
    name: str
    type: str
    required: bool = False
    enum: tuple[str, ...] = ()
    description: str | None = None


@dataclass(frozen=True)
class EntityDef:
    name: str
    kind: str
    load: str
    key: tuple[str, ...]
    fields: dict[str, FieldDef]
    window_field: str | None = None
    label: dict[str, str] = field(default_factory=dict)
    description: dict[str, str] = field(default_factory=dict)


class CanonicalContract:
    def __init__(self, version: str, entities: dict[str, EntityDef]) -> None:
        self.version = version
        self.entities = entities

    @classmethod
    def load(cls, path: str | Path) -> "CanonicalContract":
        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
            entities = {}
            for name, spec in raw["entities"].items():
                fields = {fname: FieldDef(fname, f["type"], bool(f.get("required")), tuple(f.get("enum") or ()),
                                          (f.get("description") or {}).get("ru"))
                          for fname, f in spec["fields"].items()}
                entities[name] = EntityDef(name, spec["kind"], spec["load"], tuple(spec["key"]), fields,
                                           spec.get("window_field"), spec.get("label") or {}, spec.get("description") or {})
            return cls(str(raw["version"]), entities)
        except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            raise ContractError(f"canonical contract cannot be loaded from {path}: {exc}") from exc

    def validate(self, entity: str, record: Any) -> list[str]:
        spec = self.entities[entity]
        if not isinstance(record, dict):
            return ["record must be an object"]
        problems = [f"unknown field '{name}'" for name in record if name not in spec.fields]
        for name, f in spec.fields.items():
            value = record.get(name)
            if value is None:
                if f.required:
                    problems.append(f"'{name}' is required")
                continue
            problem = _type_problem(f.type, value)
            if problem:
                problems.append(f"'{name}' {problem}")
            elif f.enum and value not in f.enum:
                problems.append(f"'{name}' must be one of {list(f.enum)}")
        return problems


def _type_problem(kind: str, value: Any) -> str | None:
    if kind == "string":
        if not isinstance(value, str):
            return "must be a string"
        return f"is longer than {MAX_STRING} characters" if len(value) > MAX_STRING else None
    if kind == "decimal":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return "must be a finite number"
        return None
    if kind == "integer":
        return None if isinstance(value, int) and not isinstance(value, bool) else "must be an integer"
    if kind == "boolean":
        return None if isinstance(value, bool) else "must be a boolean"
    if kind == "date":
        if not isinstance(value, str) or not _DATE.match(value):
            return "must be a date YYYY-MM-DD"
        try:
            date.fromisoformat(value)
        except ValueError:
            return "must be a valid date"
        return None
    return f"has unsupported contract type {kind}"
