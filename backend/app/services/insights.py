"""AI Insights: deterministic facts (SQL/Python) → LLM wording → number check → template fallback (§17)."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any

from app.agent.grounding import unverified_numbers
from app.llm.base import LLMError, LLMMessage, LLMProvider
from app.semantic.loader import SemanticLayer
from app.semantic.models import DimensionMoversRule, MetricChangeRule, SqlCheckRule
from app.services.dashboard import DashboardService
from app.services.formatting import format_change_pct, format_value
from app.services.metrics import MetricError, MetricQueryError, MetricService
from app.sql.executor import SafeSqlExecutor

logger = logging.getLogger(__name__)

_PRIORITY = {"negative": 0, "warning": 1, "positive": 2, "info": 3}

WORDING_PROMPT = """\
You write short AI insights for a company executive dashboard. You receive facts that were already calculated \
by the system. For each insight write a title (max 8 words) and a text (1–2 sentences) in {language}.
Rules: use ONLY numbers present in the facts (you may round them the same way as in "formatted" fields); do not \
compute new numbers; do not invent causes that are not in the facts; be concrete and calm.
Return ONLY a JSON array: [{{"id": "...", "title": "...", "text": "..."}}]."""


class InsightsService:
    def __init__(self, semantic: SemanticLayer, metrics: MetricService, executor: SafeSqlExecutor,
                 dashboard: DashboardService, provider: LLMProvider | None, cache_seconds: int) -> None:
        self.semantic = semantic
        self.metrics = metrics
        self.executor = executor
        self.dashboard = dashboard
        self.provider = provider
        self.cache_seconds = cache_seconds
        self._cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}

    async def build(self, period_key: str | None, today: date, lang: str) -> dict[str, Any]:
        period, previous, _ = self.dashboard.resolve_period(period_key, today, lang)
        cache_key = (period.key, lang, today.isoformat())
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self.cache_seconds:
            return cached[1]

        candidates = await self._facts(period.start, period.end, previous.start, previous.end, period.label(lang),
                                       previous.label(lang), lang)
        candidates.sort(key=lambda c: _PRIORITY.get(c["severity"], 9))
        insights = candidates[: self.semantic.insights.max_insights]
        source = await self._apply_llm_wording(insights, lang)
        for item in insights:
            item.pop("_numbers", None)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "period": {"key": period.key, "label": period.label(lang)},
            "source": source,
            "insights": insights,
        }
        self._cache[cache_key] = (time.monotonic(), payload)
        return payload

    # ------------------------------------------------------------------ facts

    async def _facts(self, start: date, end: date, cmp_start: date, cmp_end: date, period_label: str,
                     compare_label: str, lang: str) -> list[dict[str, Any]]:
        symbol = self.semantic.business_context.currency.symbol
        items: list[dict[str, Any]] = []
        for rule in self.semantic.insights.rules:
            try:
                if isinstance(rule, MetricChangeRule):
                    items += await self._metric_change(rule, start, end, cmp_start, cmp_end, period_label, compare_label, lang, symbol)
                elif isinstance(rule, DimensionMoversRule):
                    items += await self._movers(rule, start, end, cmp_start, cmp_end, period_label, compare_label, lang, symbol)
                elif isinstance(rule, SqlCheckRule):
                    items += await self._sql_check(rule, lang, symbol)
            except (MetricError, MetricQueryError) as exc:
                logger.warning("insight rule failed", extra={"rule": rule.type, "error": str(exc)})
        return items

    async def _metric_change(self, rule: MetricChangeRule, start: date, end: date, cmp_start: date, cmp_end: date,
                             period_label: str, compare_label: str, lang: str, symbol: str) -> list[dict[str, Any]]:
        metric = self.metrics.metric(rule.metric)
        comparison = await self.metrics.compare(rule.metric, start, end, cmp_start, cmp_end)
        row = comparison.rows[0] if comparison.rows else None
        if row is None or row.change_pct is None or abs(row.change_pct) < rule.min_abs_change_pct:
            return []
        up = row.change_pct > 0
        good = up == (metric.better == "higher")
        label = metric.label.get(lang)
        value_text = format_value(row.value, metric.unit, lang, symbol)
        compare_text = format_value(row.compare_value, metric.unit, lang, symbol)
        change_text = format_change_pct(row.change_pct, lang)
        if lang == "en":
            title = f"{label} {'up' if up else 'down'} {change_text.lstrip('+')}"
            text = f"{label}: {value_text} in {period_label} vs {compare_text} in {compare_label} ({change_text})."
        else:
            title = f"{label}: {'рост' if up else 'снижение'} на {change_text.lstrip('+').replace(MINUS_SIGN, '')}"
            text = f"{label} за {period_label.lower()} — {value_text} против {compare_text} за {compare_label.lower()} ({change_text})."
        question = rule.suggested_question.get(lang) if rule.suggested_question else (
            f"Why did {label.lower()} change in {period_label}?" if lang == "en"
            else f"Почему {'выросла' if up else 'снизилась'} {label.lower()} за {period_label.lower()}?")
        return [{
            "id": f"{rule.metric}_change", "severity": "positive" if good else "negative", "title": title, "text": text,
            "facts": [{"label": label, "value": row.value, "unit": metric.unit, "change_pct": row.change_pct,
                       "compare_value": row.compare_value, "formatted": value_text, "compare_formatted": compare_text,
                       "change_formatted": change_text, "period": period_label, "compare_period": compare_label}],
            "suggested_question": question,
            "_numbers": [row.value, row.compare_value, row.change_pct],
        }]

    async def _movers(self, rule: DimensionMoversRule, start: date, end: date, cmp_start: date, cmp_end: date,
                      period_label: str, compare_label: str, lang: str, symbol: str) -> list[dict[str, Any]]:
        metric = self.metrics.metric(rule.metric)
        comparison = await self.metrics.compare(rule.metric, start, end, cmp_start, cmp_end, group_by=rule.dimension)
        movers = [r for r in comparison.rows
                  if r.change_pct is not None and r.change_abs is not None and abs(r.change_pct) >= rule.min_abs_change_pct]
        movers.sort(key=lambda r: abs(r.change_abs or 0), reverse=True)
        dimension_label = self.semantic.dimension_label(rule.dimension, lang)
        items = []
        for r in movers[: rule.top]:
            up = (r.change_pct or 0) > 0
            good = up == (metric.better == "higher")
            label = metric.label.get(lang)
            change_text = format_change_pct(r.change_pct, lang)
            value_text = format_value(r.value, metric.unit, lang, symbol)
            if lang == "en":
                title = f"{r.dimension}: {label.lower()} {change_text}"
                text = f"{dimension_label} «{r.dimension}»: {label.lower()} {value_text} in {period_label} ({change_text} vs {compare_label})."
                question = f"Why did {label.lower()} in {r.dimension} change?"
            else:
                title = f"{r.dimension}: {label.lower()} {change_text}"
                text = f"{dimension_label} «{r.dimension}»: {label.lower()} {value_text} за {period_label.lower()} ({change_text} к {compare_label.lower()})."
                question = f"Почему изменилась {label.lower()} по «{r.dimension}»?"
            items.append({
                "id": f"{rule.metric}_{rule.dimension}_{r.dimension}", "severity": "positive" if good else "negative",
                "title": title, "text": text,
                "facts": [{"label": f"{label} — {r.dimension}", "value": r.value, "unit": metric.unit,
                           "change_pct": r.change_pct, "compare_value": r.compare_value, "formatted": value_text,
                           "change_formatted": change_text}],
                "suggested_question": question,
                "_numbers": [r.value, r.compare_value, r.change_pct, r.change_abs],
            })
        return items

    async def _sql_check(self, rule: SqlCheckRule, lang: str, symbol: str) -> list[dict[str, Any]]:
        outcome = await self.executor.execute(rule.sql, purpose=f"insight:{rule.key}", max_rows=10)
        if not outcome.ok or not outcome.result or not outcome.result.rows:
            if not outcome.ok:
                logger.warning("insight sql_check failed", extra={"rule": rule.key,
                                                                  "error": outcome.error.message if outcome.error else outcome.validation.message})
            return []
        rows = outcome.result.rows
        numbers: list[float] = []
        described = []
        for row in rows[:3]:
            parts = []
            for key, value in row.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numbers.append(float(value))
                    shown = format_value(value, "currency" if key in ("amount", "revenue") else "number", lang, symbol)
                    parts.append(f"{key}: {shown}")
                else:
                    parts.append(str(value))
            described.append(", ".join(parts))
        more = len(rows) - 3
        text = "; ".join(described)
        if more > 0:
            text += f" (+{more} more)" if lang == "en" else f" (и ещё {more})"
        return [{
            "id": rule.key, "severity": rule.severity, "title": rule.title.get(lang), "text": text + ".",
            "facts": rows, "suggested_question": rule.suggested_question.get(lang) if rule.suggested_question else None,
            "_numbers": numbers,
        }]

    # ------------------------------------------------------------------ wording

    async def _apply_llm_wording(self, insights: list[dict[str, Any]], lang: str) -> str:
        if not insights or self.provider is None or not self.provider.configured:
            return "deterministic"
        facts = [{"id": i["id"], "severity": i["severity"], "draft_title": i["title"], "draft_text": i["text"],
                  "facts": i["facts"]} for i in insights]
        prompt = WORDING_PROMPT.format(language="English" if lang == "en" else "Russian")
        try:
            response = await self.provider.complete(
                system=prompt, messages=[LLMMessage.user(json.dumps(facts, ensure_ascii=False, default=str))],
                tools=[], max_output_tokens=4000)
        except LLMError as exc:
            logger.warning("insight wording unavailable", extra={"error": exc.code})
            return "deterministic"
        match = re.search(r"\[.*\]", response.text, re.DOTALL)
        if not match:
            return "deterministic"
        try:
            worded = {str(w.get("id")): w for w in json.loads(match.group(0)) if isinstance(w, dict)}
        except json.JSONDecodeError:
            return "deterministic"
        used = False
        for item in insights:
            candidate = worded.get(item["id"])
            if not candidate or not candidate.get("title") or not candidate.get("text"):
                continue
            allowed = [float(n) for n in item.get("_numbers", []) if isinstance(n, (int, float))]
            allowed += _numbers_in(item["facts"])
            if unverified_numbers([str(candidate["title"]), str(candidate["text"])], allowed):
                continue  # the model changed numbers — keep the deterministic text
            item["title"], item["text"] = str(candidate["title"]), str(candidate["text"])
            used = True
        return "llm" if used else "deterministic"


MINUS_SIGN = "−"


def _numbers_in(value: Any) -> list[float]:
    found: list[float] = []
    if isinstance(value, dict):
        for v in value.values():
            found += _numbers_in(v)
    elif isinstance(value, list):
        for v in value:
            found += _numbers_in(v)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        found.append(float(value))
    return found
