"""1C adapter: extracts mapped objects through the read-only OData client and produces canonical records."""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from biznext_connector import VERSION
from biznext_connector.adapters import SnapshotPolicy, Window
from biznext_connector.contract import Contract
from biznext_connector.onec.mapping import (
    EnrichSpec,
    Lookups,
    Mapping,
    MappingError,
    RowContext,
    SourceSpec,
    evaluate,
    lookups_of,
    normalize_key,
    paths_of,
)
from biznext_connector.onec.odata import ODataClient

logger = logging.getLogger(__name__)


def month_windows(window: Window) -> list[Window]:
    """Split [start, end) into calendar-month pieces (bounded memory and page counts per request)."""
    pieces: list[Window] = []
    current = window.start
    while current < window.end:
        last = calendar.monthrange(current.year, current.month)[1]
        next_month = date(current.year, current.month, last) + timedelta(days=1)
        pieces.append(Window(current, min(next_month, window.end)))
        current = next_month
    return pieces


def _dt(d: date) -> str:
    return f"datetime'{d.isoformat()}T00:00:00'"


class OneCAdapter:
    system = "1c"

    def __init__(self, client: ODataClient, mapping: Mapping, contract: Contract, *, entities: list[str] | None = None,
                 excluded: dict[str, set[str]] | None = None) -> None:
        self.client = client
        self.mapping = mapping
        self.contract = contract
        self.excluded = excluded or {}
        wanted = entities or list(mapping.entities)
        self.specs: dict[str, list[SourceSpec]] = {}
        for entity in wanted:
            if entity not in mapping.entities:
                raise MappingError(f"entity '{entity}' is not mapped in {mapping.configuration}")
            contract_fields = contract.entity(entity).fields
            specs = []
            for spec in mapping.entities[entity]:
                unknown = [f for f in spec.fields if f not in contract_fields]
                if unknown:
                    raise MappingError(f"{entity}: fields {unknown} are not in contract v{contract.version}")
                drop = self.excluded.get(entity, set())
                spec.fields = {name: expr for name, expr in spec.fields.items() if name not in drop}
                specs.append(spec)
            self.specs[entity] = specs
        self._lookups: Lookups | None = None

    # ------------------------------------------------------------------ description

    def entities(self) -> list[str]:
        return list(self.specs)

    def snapshot_policy(self, entity: str) -> SnapshotPolicy:
        for spec in self.specs.get(entity, []):
            if spec.snapshots:
                return spec.snapshots
        return SnapshotPolicy()

    def describe(self) -> dict[str, Any]:
        parts = urlsplit(self.client.base_url)
        return {
            "system": "1c",
            "adapter": "odata_standard",
            "configuration": self.mapping.configuration,
            "endpoint": f"{parts.scheme}://{parts.hostname}{f':{parts.port}' if parts.port else ''}{parts.path}",
            "read_only": True,
            "connector_version": VERSION,
            "contract_version": self.contract.version,
            "entities": {
                entity: {
                    "objects": list(dict.fromkeys(o for spec in specs for o in spec.referenced_objects())),
                    "fields": sorted({f for spec in specs for f in spec.fields}),
                    "excluded_fields": sorted(self.excluded.get(entity, set())),
                }
                for entity, specs in self.specs.items()
            },
        }

    async def check(self) -> dict[str, Any]:
        published, configuration = await self.client.published_objects()
        required = sorted({o for specs in self.specs.values() for spec in specs for o in spec.referenced_objects()})
        missing = [o for o in required if o not in published]
        counts: dict[str, int] = {}
        for specs in self.specs.values():
            for spec in specs:
                if spec.object and spec.object in published and spec.object not in counts:
                    counts[spec.object] = await self.client.count(spec.object, spec.filter)
        return {"ok": not missing, "configuration": configuration, "required_objects": required, "missing_objects": missing,
                "counts": counts}

    async def close(self) -> None:
        await self.client.close()

    # ------------------------------------------------------------------ lookups

    def reset(self) -> None:
        """Forget cached catalogs; called at the start of every sync run."""
        self._lookups = None

    async def lookups(self) -> Lookups:
        if self._lookups is not None:
            return self._lookups
        needs: dict[tuple[str, tuple[str, ...]], set[str]] = defaultdict(set)
        for specs in self.specs.values():
            for spec in specs:
                for expr in spec.fields.values():
                    for obj, key_fields, attr in lookups_of(expr):
                        needs[(obj, key_fields)].add(attr)
                if spec.complete_from:
                    needs[(spec.complete_from.object, spec.complete_from.key)] |= set()
        lookups = Lookups()
        for (obj, key_fields), attrs in sorted(needs.items()):
            select = list(dict.fromkeys([*key_fields, *sorted(attrs)]))
            order = "Ref_Key" if key_fields == ("Ref_Key",) else ",".join(key_fields)
            rows = await self.client.all(obj, select=select, order_by=order)
            lookups.add(obj, key_fields, rows)
            logger.info("lookup loaded", extra={"object": obj, "rows": len(rows)})
        self._lookups = lookups
        return lookups

    # ------------------------------------------------------------------ extraction

    async def extract(self, entity: str, *, window: Window | None = None,
                      snapshot_dates: list[date] | None = None) -> AsyncIterator[list[dict[str, Any]]]:
        lookups = await self.lookups()
        for spec in self.specs[entity]:
            if spec.balance:
                async for page in self._balances(spec, snapshot_dates or [], lookups):
                    yield page
            elif spec.lines:
                if window is None:
                    raise ValueError(f"{entity}: documents need a date window")
                async for page in self._documents(spec, window, lookups):
                    yield page
            else:
                async for page in self._catalog(spec, lookups):
                    yield page

    @staticmethod
    def _select(spec: SourceSpec) -> list[str]:
        paths: set[str] = {"Ref_Key"}
        for expr in spec.fields.values():
            paths |= paths_of(expr)
        if spec.date:
            paths.add(spec.date)
        for e in spec.enrich:
            paths |= set(e.match.values())
            if e.allocate_line:
                paths.add(e.allocate_line)
        header = sorted(p for p in paths if not p.startswith("line."))
        lines = sorted(p[5:] for p in paths if p.startswith("line."))
        select = header + [f"{spec.lines}/{name}" for name in lines if name not in ("Ref_Key",)]
        if spec.lines and not lines:
            select.append(spec.lines)
        return select

    async def _catalog(self, spec: SourceSpec, lookups: Lookups) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self.client.pages(spec.object or "", filter_=spec.filter, select=self._select(spec)):
            yield [{name: evaluate(expr, RowContext(row, None, lookups)) for name, expr in spec.fields.items()} for row in page]

    async def _documents(self, spec: SourceSpec, window: Window, lookups: Lookups) -> AsyncIterator[list[dict[str, Any]]]:
        select = self._select(spec)
        for piece in month_windows(window):
            date_filter = f"{spec.date} ge {_dt(piece.start)} and {spec.date} lt {_dt(piece.end)}"
            flt = f"({spec.filter}) and {date_filter}" if spec.filter else date_filter
            enriched = {e.name: await self._enrichment(e, piece) for e in spec.enrich}
            async for page in self.client.pages(spec.object or "", filter_=flt, select=select):
                records: list[dict[str, Any]] = []
                for doc in page:
                    lines = doc.get(spec.lines or "") or []
                    line_totals = {e.name: self._line_totals(e, doc, lines, lookups) for e in spec.enrich}
                    for line in lines:
                        ctx = RowContext(doc, line, lookups)
                        for e in spec.enrich:
                            ctx.enriched[e.name] = self._enriched_value(e, ctx, enriched[e.name], line_totals[e.name])
                        records.append({name: evaluate(expr, ctx) for name, expr in spec.fields.items()})
                yield records

    async def _enrichment(self, e: EnrichSpec, piece: Window) -> dict[tuple[str | None, ...], list[float]]:
        date_filter = f"{e.date} ge {_dt(piece.start)} and {e.date} lt {_dt(piece.end)}"
        flt = f"({e.filter}) and {date_filter}" if e.filter else date_filter
        select = list(dict.fromkeys([*e.match, e.sum, *([e.allocate_register] if e.allocate_register else [])]))
        totals: dict[tuple[str | None, ...], list[float]] = defaultdict(lambda: [0.0, 0.0])
        async for page in self.client.pages(e.object, filter_=flt, select=select, order_by=e.order_by):
            for row in page:
                bucket = totals[tuple(normalize_key(row.get(f)) for f in e.match)]
                bucket[0] += float(row.get(e.sum) or 0)
                if e.allocate_register:
                    bucket[1] += float(row.get(e.allocate_register) or 0)
        return dict(totals)

    @staticmethod
    def _line_totals(e: EnrichSpec, doc: dict[str, Any], lines: list[dict[str, Any]], lookups: Lookups) -> dict[tuple[str | None, ...], float]:
        totals: dict[tuple[str | None, ...], float] = defaultdict(float)
        if not e.allocate_line:
            return totals
        for line in lines:
            ctx = RowContext(doc, line, lookups)
            totals[tuple(normalize_key(ctx.path(p)) for p in e.match.values())] += float(ctx.path(e.allocate_line) or 0)
        return totals

    @staticmethod
    def _enriched_value(e: EnrichSpec, ctx: RowContext, totals: dict[tuple[str | None, ...], list[float]],
                        line_totals: dict[tuple[str | None, ...], float]) -> float | None:
        key = tuple(normalize_key(ctx.path(p)) for p in e.match.values())
        bucket = totals.get(key)
        if bucket is None:
            return None
        if e.allocate_line and line_totals.get(key):
            share = float(ctx.path(e.allocate_line) or 0) / line_totals[key]
            return round(bucket[0] * share, 4)
        return round(bucket[0], 4)

    async def _balances(self, spec: SourceSpec, dates: list[date], lookups: Lookups) -> AsyncIterator[list[dict[str, Any]]]:
        for snapshot in dates:
            rows = await self.client.balance(spec.balance or "", f"{snapshot.isoformat()}T23:59:59")
            records: list[dict[str, Any]] = []
            seen: set[tuple[str | None, ...]] = set()
            complete = spec.complete_from
            for row in rows:
                record = {name: evaluate(expr, RowContext(row, None, lookups)) for name, expr in spec.fields.items()}
                record["snapshot_date"] = snapshot.isoformat()
                records.append(record)
                if complete:
                    seen.add(tuple(normalize_key(row.get(k)) for k in complete.key))
            if complete:
                for key in lookups.keys(complete.object, complete.key):
                    if key in seen:
                        continue
                    row = dict(zip(complete.key, key, strict=True))
                    record = {name: evaluate(expr, RowContext(row, None, lookups)) for name, expr in spec.fields.items()}
                    for name, value in complete.defaults.items():
                        if record.get(name) is None:
                            record[name] = value
                    record["snapshot_date"] = snapshot.isoformat()
                    records.append(record)
            yield records
