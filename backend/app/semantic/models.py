from __future__ import annotations

import ast
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Unit = Literal["currency", "number", "percent", "hours", "liters", "kg"]
Better = Literal["higher", "lower"]
MetricKind = Literal["simple", "ratio", "formula", "snapshot"]
Granularity = Literal["day", "week", "month", "quarter", "year"]
ChartType = Literal["line", "bar", "area", "pie"]
Severity = Literal["positive", "negative", "warning", "info"]

DASHBOARD_PERIOD_KEYS = (
    "month_to_date",
    "last_month",
    "quarter_to_date",
    "last_quarter",
    "year_to_date",
    "last_30_days",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LocalizedText(_Model):
    ru: str
    en: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_plain_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"ru": value}
        return value

    def get(self, lang: str = "ru") -> str:
        if lang == "en" and self.en:
            return self.en
        return self.ru


# ---------- business_context.yaml ----------


class Company(_Model):
    name: LocalizedText
    industry: LocalizedText | None = None
    description: LocalizedText | None = None


class Currency(_Model):
    code: str
    symbol: str
    amounts_note: LocalizedText | None = None


class GlossaryItem(_Model):
    term: LocalizedText
    meaning: LocalizedText


class BusinessContext(_Model):
    version: int = 1
    company: Company
    currency: Currency
    timezone: str | None = None
    week_starts_on: Literal["monday", "sunday"] = "monday"
    glossary: list[GlossaryItem] = Field(default_factory=list)
    analysis_guidelines: list[LocalizedText] = Field(default_factory=list)


# ---------- data_dictionary.yaml ----------


class ColumnDoc(_Model):
    description: LocalizedText | None = None
    values: dict[str, LocalizedText] = Field(default_factory=dict)

    @field_validator("values", mode="before")
    @classmethod
    def _stringify_value_keys(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): v for k, v in value.items()}
        return value


class TableDoc(_Model):
    description: LocalizedText | None = None
    grain: LocalizedText | None = None
    business_meaning: LocalizedText | None = None
    columns: dict[str, ColumnDoc] = Field(default_factory=dict)

    @field_validator("columns", mode="before")
    @classmethod
    def _empty_columns(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (v if v is not None else {}) for k, v in value.items()}
        return value


class DataDictionary(_Model):
    version: int = 1
    schema_name: str = Field(default="demo", alias="schema")
    tables: dict[str, TableDoc] = Field(default_factory=dict)


# ---------- metrics.yaml ----------


class Dimension(_Model):
    label: LocalizedText
    joins: list[str] = Field(default_factory=list)
    column: str


class MetricSource(_Model):
    from_: str = Field(alias="from")
    date_column: str
    filters: list[str] = Field(default_factory=list)
    dimensions: dict[str, Dimension] = Field(default_factory=dict)


class Metric(_Model):
    label: LocalizedText
    description: LocalizedText | None = None
    synonyms: list[str] = Field(default_factory=list)
    unit: Unit
    better: Better = "higher"
    kind: MetricKind
    source: str | None = None
    expression: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    formula: str | None = None

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "Metric":
        if self.kind in ("simple", "snapshot"):
            if not self.source or not self.expression:
                raise ValueError(f"kind '{self.kind}' requires 'source' and 'expression'")
        elif self.kind == "ratio":
            if not self.numerator or not self.denominator:
                raise ValueError("kind 'ratio' requires 'numerator' and 'denominator'")
        elif self.kind == "formula":
            if not self.formula:
                raise ValueError("kind 'formula' requires 'formula'")
        return self


class MetricsCatalog(_Model):
    version: int = 1
    sources: dict[str, MetricSource]
    metrics: dict[str, Metric]


# ---------- dashboard.yaml ----------


class Sparkline(_Model):
    granularity: Granularity = "month"
    points: int = 12


class KpiSpec(_Model):
    metric: str
    sparkline: Sparkline | None = None


class ChartSpec(_Model):
    key: str
    type: ChartType
    title: LocalizedText
    metrics: list[str]
    granularity: Granularity | None = None
    lookback: int | None = None
    group_by: str | None = None
    top: int | None = None

    @model_validator(mode="after")
    def _check(self) -> "ChartSpec":
        if not self.metrics:
            raise ValueError(f"chart '{self.key}' needs at least one metric")
        return self


class DashboardConfig(_Model):
    version: int = 1
    default_period: str = "last_month"
    periods: list[str] = Field(default_factory=lambda: list(DASHBOARD_PERIOD_KEYS))
    kpis: list[KpiSpec] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_periods(self) -> "DashboardConfig":
        unknown = [p for p in self.periods if p not in DASHBOARD_PERIOD_KEYS]
        if unknown:
            raise ValueError(f"unknown periods {unknown}; supported: {list(DASHBOARD_PERIOD_KEYS)}")
        if self.default_period not in self.periods:
            raise ValueError(f"default_period '{self.default_period}' must be one of periods")
        return self


# ---------- insights.yaml ----------


class MetricChangeRule(_Model):
    type: Literal["metric_change"]
    metric: str
    min_abs_change_pct: float = 0.0
    suggested_question: LocalizedText | None = None


class DimensionMoversRule(_Model):
    type: Literal["dimension_movers"]
    metric: str
    dimension: str
    top: int = 2
    min_abs_change_pct: float = 0.0
    suggested_question: LocalizedText | None = None


class SqlCheckRule(_Model):
    type: Literal["sql_check"]
    key: str
    severity: Severity = "info"
    title: LocalizedText
    sql: str
    text: LocalizedText | None = None
    unit: Unit | None = None
    suggested_question: LocalizedText | None = None


InsightRule = Annotated[Union[MetricChangeRule, DimensionMoversRule, SqlCheckRule], Field(discriminator="type")]


class InsightsConfig(_Model):
    version: int = 1
    max_insights: int = 5
    rules: list[InsightRule] = Field(default_factory=list)


# ---------- suggested_questions.yaml ----------


class SuggestedQuestion(_Model):
    text: LocalizedText
    category: str = "general"


class SuggestedQuestionsConfig(_Model):
    version: int = 1
    questions: list[SuggestedQuestion] = Field(default_factory=list)


# ---------- formula parsing ----------

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def parse_formula(formula: str) -> ast.expr:
    """Parse a metric formula: only + - * /, parentheses, numbers and metric keys."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid formula syntax: {formula!r}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)) or isinstance(node, _ALLOWED_BINOPS):
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.Name):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            continue
        raise ValueError(f"formula {formula!r} contains a forbidden element: {type(node).__name__}")
    return tree.body


# Variables provided by the metric engine itself (not metric keys): number of days in the period
# or in the time bucket, e.g. turnover in days = stock / units_sold * period_days.
FORMULA_VARIABLES = ("period_days",)


def formula_metric_refs(formula: str) -> list[str]:
    body = parse_formula(formula)
    refs: list[str] = []
    for node in ast.walk(body):
        if isinstance(node, ast.Name) and node.id not in refs and node.id not in FORMULA_VARIABLES:
            refs.append(node.id)
    return refs
