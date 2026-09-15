"""Sync planning and execution: full reload once a day, incremental windows in between."""

from __future__ import annotations

import asyncio
import calendar
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from biznext_connector.adapters import SnapshotPolicy, SourceAdapter, Window
from biznext_connector.biznext import BizNextClient
from biznext_connector.config import Settings
from biznext_connector.contract import Contract

logger = logging.getLogger(__name__)

KIND_ORDER = {"catalog": 0, "document": 1, "snapshot": 2}


@dataclass
class LoadPlan:
    entity: str
    mode: str
    window: Window | None = None
    snapshot_dates: list[date] = field(default_factory=list)


def add_months(d: date, months: int) -> date:
    year, month = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(year, month + 1, 1)


def snapshot_dates(policy: SnapshotPolicy, start: date, today: date, full: bool) -> list[date]:
    recent = [today - timedelta(days=i) for i in range(max(policy.recent_days, 1))]
    if not full:
        recent = recent[:2]
    dates = set(recent)
    if full and policy.history == "month_end":
        current = start
        while current <= today:
            last = date(current.year, current.month, calendar.monthrange(current.year, current.month)[1])
            if last < today:
                dates.add(last)
            current = add_months(current, 1)
    return sorted(dates)


def build_plan(contract: Contract, adapter: SourceAdapter, *, full: bool, today: date, history_months: int,
               lookback_days: int) -> list[LoadPlan]:
    tomorrow = today + timedelta(days=1)
    history_start = add_months(today.replace(day=1), -history_months)
    plans: list[LoadPlan] = []
    for entity in sorted(adapter.entities(), key=lambda e: (KIND_ORDER[contract.entity(e).kind], e)):
        spec = contract.entity(entity)
        if spec.load == "replace_all":
            plans.append(LoadPlan(entity, "replace_all"))
        elif spec.kind == "snapshot":
            dates = snapshot_dates(adapter.snapshot_policy(entity), history_start, today, full)
            plans.append(LoadPlan(entity, "replace_window", Window(dates[0] if not full else history_start, tomorrow), dates))
        else:
            start = history_start if full else today - timedelta(days=lookback_days)
            plans.append(LoadPlan(entity, "replace_window", Window(start, tomorrow)))
    return plans


class SyncRunner:
    def __init__(self, settings: Settings, contract: Contract, adapter: SourceAdapter, biznext: BizNextClient) -> None:
        self.settings = settings
        self.contract = contract
        self.adapter = adapter
        self.biznext = biznext
        self.tz = ZoneInfo(settings.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def full_due(self, state: dict[str, Any], now: datetime) -> bool:
        last_full = state.get("last_full_success_at")
        if not last_full:
            return True
        last = datetime.fromisoformat(last_full).astimezone(self.tz)
        hour, _, minute = self.settings.full_sync_time.partition(":")
        scheduled = now.replace(hour=int(hour), minute=int(minute or 0), second=0, microsecond=0)
        if now < scheduled:
            scheduled -= timedelta(days=1)
        return last < scheduled

    async def run_once(self, kind: str = "auto") -> dict[str, Any]:
        now = self.now()
        if kind == "auto":
            kind = "full" if self.full_due(await self.biznext.state(), now) else "incremental"
        plans = build_plan(self.contract, self.adapter, full=kind == "full", today=now.date(),
                           history_months=self.settings.history_months, lookback_days=self.settings.lookback_days)
        reset = getattr(self.adapter, "reset", None)
        if callable(reset):
            reset()
        run_id = await self.biznext.start_run(kind, self.adapter.describe())
        started = time.monotonic()
        stats: dict[str, Any] = {"entities": {}}
        errors: list[str] = []
        logger.info("sync started", extra={"run_id": run_id, "kind": kind, "entities": len(plans)})
        for plan in plans:
            try:
                stats["entities"][plan.entity] = await self._load(run_id, plan)
            except Exception as exc:  # noqa: BLE001 - one entity failing must not stop the others
                message = f"{plan.entity}: {exc}"
                errors.append(message)
                stats["entities"][plan.entity] = {"status": "failed", "error": str(exc)[:500]}
                logger.error("entity sync failed", extra={"run_id": run_id, "entity": plan.entity, "error": str(exc)[:500]})
        stats["duration_s"] = round(time.monotonic() - started, 1)
        status = "failed" if errors else "succeeded"
        await self.biznext.finish_run(run_id, status, stats, "; ".join(errors)[:2000] or None)
        logger.info("sync finished", extra={"run_id": run_id, "kind": kind, "status": status, "duration_s": stats["duration_s"]})
        return {"run_id": run_id, "kind": kind, "status": status, **stats}

    async def _load(self, run_id: str, plan: LoadPlan) -> dict[str, Any]:
        started = time.monotonic()
        window_from = plan.window.start.isoformat() if plan.window else None
        window_to = plan.window.end.isoformat() if plan.window else None
        load_id = await self.biznext.open_load(run_id, plan.entity, plan.mode, window_from, window_to)
        buffer: list[dict[str, Any]] = []
        seq = sent = rejected = 0
        samples: list[str] = []

        async def flush() -> None:
            nonlocal seq, sent, buffer
            if buffer:
                seq += 1
                sent += await self.biznext.send_chunk(load_id, seq, buffer)
                buffer = []

        async for page in self.adapter.extract(plan.entity, window=plan.window, snapshot_dates=plan.snapshot_dates or None):
            for raw in page:
                record = self.contract.coerce(plan.entity, raw)
                problems = self.contract.problems(plan.entity, record)
                if problems:
                    rejected += 1
                    if len(samples) < 3:
                        samples.append("; ".join(problems))
                    continue
                buffer.append(record)
                if len(buffer) >= self.settings.chunk_size:
                    await flush()
        await flush()
        result = await self.biznext.commit(load_id, sent)
        outcome = {"status": "committed", "records": sent, "rejected": rejected, "inserted": result.get("inserted"),
                   "deleted": result.get("deleted"), "seconds": round(time.monotonic() - started, 1)}
        if samples:
            outcome["rejected_samples"] = samples
        logger.info("entity synced", extra={"entity": plan.entity, **{k: v for k, v in outcome.items() if k != "rejected_samples"}})
        return outcome

    async def loop(self, stop: asyncio.Event) -> None:
        interval = max(self.settings.sync_interval_minutes, 1) * 60
        while not stop.is_set():
            try:
                await self.run_once("auto")
            except Exception as exc:  # noqa: BLE001 - keep the service alive, retry next interval
                logger.error("sync run failed", extra={"error": str(exc)[:500]})
                interval_now = min(interval, 300)
            else:
                interval_now = interval
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_now)
            except asyncio.TimeoutError:
                continue
