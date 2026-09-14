"""End-to-end questions with the real LLM provider (costs money): pytest -m live_llm"""

from __future__ import annotations

import os

import pytest

from app.agent.date_context import today_in
from app.config import get_settings
from app.container import build_container

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(not os.environ.get("LLM_API_KEY") or not os.environ.get("AGENT_DATABASE_URL"),
                       reason="LLM_API_KEY / AGENT_DATABASE_URL not set"),
]

QUESTIONS = [
    ("Сколько мы продали в прошлом месяце?", {"revenue"}),
    ("Какие категории растут быстрее всего за последние полгода?", set()),
    ("Какие цены у наших конкурентов?", set()),
]


@pytest.mark.parametrize(("question", "_expected_metrics"), QUESTIONS)
async def test_live_question(question: str, _expected_metrics: set[str]) -> None:
    get_settings.cache_clear()
    container = build_container(get_settings())
    services = container.require()
    try:
        run = await services.agent.run(question, today=today_in(container.settings.app_timezone))
    finally:
        await container.close()
    assert run.status in ("answered", "insufficient_data", "clarification_needed"), (run.status, run.error_message)
    assert run.response and run.response["answer"]
    if "конкурентов" in question:
        assert run.status in ("insufficient_data", "clarification_needed") or "нет" in run.response["answer"].lower()
    else:
        assert run.response["sql"], "answer must be backed by queries"
