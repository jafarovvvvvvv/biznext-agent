"""Registry of data sources (semantic/sources.yaml): which data the agent can analyze and how it arrives."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from app.semantic.loader import SemanticError, SemanticLayer, load_semantic
from app.semantic.models import LocalizedText, _Model

SOURCES_FILE = "sources.yaml"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class SourceConfig(_Model):
    type: str
    storage: Literal["direct", "canonical"]
    mode: Literal["live", "demo"] = "live"
    schema_name: str = Field(alias="schema")
    semantic: list[str]
    name: LocalizedText
    description: LocalizedText | None = None
    entities: list[str] = Field(default_factory=list)

    @field_validator("schema_name")
    @classmethod
    def _schema_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.match(value):
            raise ValueError(f"schema '{value}' must be a lowercase identifier")
        return value


class SourcesConfig(_Model):
    version: int = 1
    default: str
    sources: dict[str, SourceConfig]

    @model_validator(mode="after")
    def _check(self) -> "SourcesConfig":
        for source_id in self.sources:
            if not _IDENTIFIER.match(source_id):
                raise ValueError(f"source id '{source_id}' must be a lowercase identifier")
        if self.default not in self.sources:
            raise ValueError(f"default source '{self.default}' is not defined")
        return self


def load_sources(root: str | Path) -> SourcesConfig | None:
    """None when the semantic root has no sources.yaml (single-source layout)."""
    path = Path(root) / SOURCES_FILE
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return SourcesConfig.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise SemanticError(f"{SOURCES_FILE}: {exc}") from exc


def load_source_semantic(root: str | Path, source: SourceConfig) -> SemanticLayer:
    base = Path(root)
    return load_semantic([base / part for part in source.semantic], {"schema": source.schema_name})
