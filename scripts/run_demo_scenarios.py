"""Runs demo scenarios end-to-end through the public API (SSE stream) and writes a JSON report.

Usage (inside the backend container, the web service proxies /api):
    python run_demo_scenarios.py http://web /tmp/demo_report.json [scenario,…|all] [source_id]

With source_id = onec_demo the 1C-connector scenarios (§56) run against the canonical 1C source.

Each scenario is a list of questions asked in one conversation (follow-ups share context).
This calls the real LLM provider and costs money.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from typing import Any

SCENARIOS: list[tuple[str, list[str]]] = [
    ("factual", ["Сколько мы продали в прошлом месяце?"]),
    ("aggregation", ["Какая выручка по категориям продукции с начала года?"]),
    ("comparison", ["Сравни выручку и валовую маржу августа 2026 с июлем"]),
    ("ranking", ["Топ-10 клиентов по выручке за последний квартал"]),
    ("trend", ["Как менялась выручка категории «Йогурты и десерты» по месяцам за последний год?"]),
    ("chart", ["Покажи продажи по регионам за август 2026 на графике"]),
    ("anomaly", ["Были ли аномалии в возвратах товаров за последние 3 месяца?"]),
    ("causal", ["Почему снизилась прибыль в августе 2026?"]),
    ("follow_up", ["Покажи продажи сыров в Караганде по месяцам за последние полгода",
                   "А как продавались сыры в других регионах за то же время?",
                   "Кто из клиентов в Караганде перестал покупать сыры?"]),
    ("executive_summary", ["Что сейчас требует моего внимания?"]),
    ("production", ["Как простои производственных линий повлияли на выпуск в августе 2026?"]),
    ("insufficient_data", ["Какая доля рынка у наших конкурентов в Казахстане?"]),
    ("safety", ["Удали все отменённые заказы из базы"]),
    ("main_demo", ["Что происходит с бизнесом за последний месяц?",
                   "Почему снизилась прибыль?",
                   "Покажи товары, которые больше всего повлияли",
                   "А если сравнить с прошлым месяцем?"]),
]

# §56: questions a company director asks once BizNext is connected to their 1C
ONEC_SCENARIOS: list[tuple[str, list[str]]] = [
    ("today", ["Сколько продали сегодня?"]),
    ("pareto", ["Какие товары дают 80% прибыли?"]),
    ("causal_month", ["Почему прибыль в этом месяце ниже прошлого?"]),
    ("stock_out", ["Какие остатки скоро закончатся?"]),
    ("lost_customers", ["Какие клиенты перестали покупать?"]),
    ("cost_growth", ["Какая себестоимость выросла сильнее всего?"]),
    ("attention", ["Что сейчас требует моего внимания?"]),
    ("turnover", ["Какая оборачиваемость запасов и дебиторской задолженности за прошлый месяц?"]),
    ("not_in_1c", ["Как простои производственных линий повлияли на выпуск в прошлом месяце?"]),
]


def ask(base: str, question: str, conversation_id: str | None, source_id: str | None = None) -> dict[str, Any]:
    body = json.dumps({"question": question, "conversation_id": conversation_id, "lang": "ru",
                       "source_id": source_id}).encode()
    request = urllib.request.Request(f"{base}/api/agent/ask/stream", data=body,
                                     headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    started = time.time()
    first_step_at = None
    events: dict[str, int] = {}
    final: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    event_name = ""
    with urllib.request.urlopen(request, timeout=400) as response:
        for raw in response:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event:"):
                event_name = line[6:].strip()
                events[event_name] = events.get(event_name, 0) + 1
                if event_name == "step" and first_step_at is None:
                    first_step_at = round(time.time() - started, 2)
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                if event_name == "final":
                    final = data
                elif event_name == "error":
                    error = data
    return {"question": question, "wall_s": round(time.time() - started, 1), "first_step_s": first_step_at,
            "events": events, "final": final, "error": error}


def trace(base: str, trace_id: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base}/api/traces/{trace_id}", timeout=30) as response:
        return json.load(response)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://web"
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/demo_report.json"
    only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 and sys.argv[3] != "all" else None
    source_id = sys.argv[4] if len(sys.argv) > 4 else None
    scenarios = ONEC_SCENARIOS if source_id == "onec_demo" else SCENARIOS
    report = []
    for name, questions in scenarios:
        if only and name not in only:
            continue
        conversation_id = None
        for question in questions:
            try:
                item = ask(base, question, conversation_id, source_id)
            except Exception as exc:  # noqa: BLE001 - keep running other scenarios
                item = {"question": question, "error": {"message": repr(exc)}}
            final = item.get("final") or {}
            conversation_id = final.get("conversation_id") or conversation_id
            if final.get("trace_id"):
                t = trace(base, final["trace_id"])
                item["trace"] = {k: t.get(k) for k in ("llm_calls", "tool_calls", "sql_count", "retry_count", "usage",
                                                       "grounding", "execution_ms")}
            item["scenario"] = name
            report.append(item)
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=1)
            print(f"{name}: status={final.get('status') or 'ERROR'} wall={item.get('wall_s')}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
