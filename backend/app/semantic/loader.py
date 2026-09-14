from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from app.semantic.models import (
    BusinessContext,
    DashboardConfig,
    DataDictionary,
    DimensionMoversRule,
    InsightsConfig,
    Metric,
    MetricChangeRule,
    MetricsCatalog,
    SuggestedQuestionsConfig,
    formula_metric_refs,
)

FILES = {
    "business_context": "business_context.yaml",
    "data_dictionary": "data_dictionary.yaml",
    "metrics": "metrics.yaml",
    "dashboard": "dashboard.yaml",
    "insights": "insights.yaml",
    "suggested_questions": "suggested_questions.yaml",
}

M = TypeVar("M", bound=BaseModel)


class SemanticError(RuntimeError):
    """Semantic layer files are missing or do not match the contract (ARCHITECTURE.md §6)."""


@dataclass(frozen=True)
class SemanticLayer:
    business_context: BusinessContext
    data_dictionary: DataDictionary
    metrics: MetricsCatalog
    dashboard: DashboardConfig
    insights: InsightsConfig
    suggested_questions: SuggestedQuestionsConfig

    def metric(self, key: str) -> Metric:
        try:
            return self.metrics.metrics[key]
        except KeyError as exc:
            raise KeyError(f"unknown metric '{key}'") from exc

    def component_metrics(self, key: str) -> list[str]:
        metric = self.metric(key)
        if metric.kind == "ratio":
            return [metric.numerator or "", metric.denominator or ""]
        if metric.kind == "formula":
            return formula_metric_refs(metric.formula or "")
        return []

    def metric_dimensions(self, key: str, _seen: frozenset[str] = frozenset()) -> list[str]:
        """Dimensions available for a metric; for ratio/formula — the intersection over components."""
        if key in _seen:
            return []
        metric = self.metric(key)
        if metric.kind in ("simple", "snapshot"):
            source = self.metrics.sources[metric.source or ""]
            return list(source.dimensions)
        dims: list[str] | None = None
        for component in self.component_metrics(key):
            component_dims = self.metric_dimensions(component, _seen | {key})
            dims = component_dims if dims is None else [d for d in dims if d in component_dims]
        return dims or []

    def dimension_label(self, dimension: str, lang: str = "ru") -> str:
        for source in self.metrics.sources.values():
            if dimension in source.dimensions:
                return source.dimensions[dimension].label.get(lang)
        return dimension


def _read_yaml(path: Path) -> object:
    if not path.is_file():
        raise SemanticError(f"semantic file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise SemanticError(f"{path.name}: invalid YAML: {exc}") from exc


def _validate(model: type[M], data: object, filename: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()[:10]
        )
        raise SemanticError(f"{filename}: {problems}") from exc


def _cross_validate(layer: SemanticLayer) -> None:
    catalog = layer.metrics
    errors: list[str] = []

    for key, metric in catalog.metrics.items():
        if metric.kind in ("simple", "snapshot") and metric.source not in catalog.sources:
            errors.append(f"metric '{key}': unknown source '{metric.source}'")
        if metric.kind == "ratio":
            for ref in (metric.numerator, metric.denominator):
                if ref not in catalog.metrics:
                    errors.append(f"metric '{key}': unknown component metric '{ref}'")
        if metric.kind == "formula":
            try:
                refs = formula_metric_refs(metric.formula or "")
            except ValueError as exc:
                errors.append(f"metric '{key}': {exc}")
                continue
            for ref in refs:
                if ref not in catalog.metrics:
                    errors.append(f"metric '{key}': formula references unknown metric '{ref}'")
    if errors:
        raise SemanticError("metrics.yaml: " + "; ".join(errors))

    def visit(key: str, stack: tuple[str, ...]) -> None:
        if key in stack:
            raise SemanticError(f"metrics.yaml: cyclic metric definition {' -> '.join(stack + (key,))}")
        for component in layer.component_metrics(key):
            visit(component, stack + (key,))

    for key in catalog.metrics:
        visit(key, ())

    def check_metric(ref: str, where: str) -> None:
        if ref not in catalog.metrics:
            errors.append(f"{where}: unknown metric '{ref}'")

    for kpi in layer.dashboard.kpis:
        check_metric(kpi.metric, "dashboard.yaml kpis")
    for chart in layer.dashboard.charts:
        for ref in chart.metrics:
            check_metric(ref, f"dashboard.yaml chart '{chart.key}'")
        if chart.group_by and not errors:
            for ref in chart.metrics:
                if chart.group_by not in layer.metric_dimensions(ref):
                    errors.append(f"dashboard.yaml chart '{chart.key}': metric '{ref}' has no dimension '{chart.group_by}'")
    for rule in layer.insights.rules:
        if isinstance(rule, (MetricChangeRule, DimensionMoversRule)):
            check_metric(rule.metric, f"insights.yaml rule {rule.type}")
        if isinstance(rule, DimensionMoversRule) and rule.metric in catalog.metrics:
            if rule.dimension not in layer.metric_dimensions(rule.metric):
                errors.append(f"insights.yaml: metric '{rule.metric}' has no dimension '{rule.dimension}'")
    if errors:
        raise SemanticError("; ".join(errors))


def load_semantic(directory: str | Path) -> SemanticLayer:
    base = Path(directory)
    if not base.is_dir():
        raise SemanticError(f"semantic directory not found: {base}")
    layer = SemanticLayer(
        business_context=_validate(BusinessContext, _read_yaml(base / FILES["business_context"]), FILES["business_context"]),
        data_dictionary=_validate(DataDictionary, _read_yaml(base / FILES["data_dictionary"]), FILES["data_dictionary"]),
        metrics=_validate(MetricsCatalog, _read_yaml(base / FILES["metrics"]), FILES["metrics"]),
        dashboard=_validate(DashboardConfig, _read_yaml(base / FILES["dashboard"]), FILES["dashboard"]),
        insights=_validate(InsightsConfig, _read_yaml(base / FILES["insights"]), FILES["insights"]),
        suggested_questions=_validate(
            SuggestedQuestionsConfig, _read_yaml(base / FILES["suggested_questions"]), FILES["suggested_questions"]
        ),
    )
    _cross_validate(layer)
    return layer
