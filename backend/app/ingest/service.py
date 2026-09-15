"""Ingest storage: runs, loads, staging and atomic replacement of canonical tables.

Runs as the dedicated INGEST_DATABASE_URL role, which can write only canonical source schemas and the
`ingest` schema. The agent role can read canonical tables but not staging; the app role sees neither.
"""

from __future__ import annotations

import hmac
import logging
import uuid
from datetime import date
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout

from app.connectors.base import DatabaseUnavailableError
from app.connectors.pg_pool import LazyPostgresPool, json_safe
from app.ingest.contract import CanonicalContract
from app.semantic.sources import SourceConfig

logger = logging.getLogger(__name__)

STALE_LOAD_HOURS = 6
ABANDONED_RUN_HOURS = 12


class IngestError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def parse_tokens(raw: str) -> dict[str, str]:
    """INGEST_TOKENS = "source_id:token,source_id2:token2" → {token: source_id}."""
    tokens: dict[str, str] = {}
    for item in raw.split(","):
        source_id, _, token = item.strip().partition(":")
        if source_id and len(token) >= 24:
            tokens[token] = source_id
        elif item.strip():
            logger.warning("ignored ingest token entry (expected source_id:token with >= 24 chars)",
                           extra={"source_id": source_id})
    return tokens


class IngestService:
    def __init__(self, conninfo: str, contract: CanonicalContract, sources: dict[str, SourceConfig],
                 tokens: dict[str, str], max_chunk_records: int = 5000) -> None:
        self.contract = contract
        self.sources = {sid: s for sid, s in sources.items() if s.storage == "canonical"}
        self._tokens = {t: sid for t, sid in tokens.items() if sid in self.sources}
        self._pool = LazyPostgresPool(conninfo, name="ingest", max_size=4)
        self.max_chunk_records = max_chunk_records

    @property
    def enabled(self) -> bool:
        return bool(self._pool.configured and self._tokens)

    def authenticate(self, token: str) -> str | None:
        for expected, source_id in self._tokens.items():
            if hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
                return source_id
        return None

    async def close(self) -> None:
        await self._pool.close()

    # ------------------------------------------------------------------ db helpers

    async def _run(self, statements: list[tuple[sql.Composable | str, Any]], *, fetch_last: bool = True) -> list[dict[str, Any]]:
        pool = await self._pool.get()
        try:
            async with pool.connection() as conn:
                async with conn.transaction():
                    rows: list[dict[str, Any]] = []
                    async with conn.cursor(row_factory=dict_row) as cur:
                        for statement, params in statements:
                            await cur.execute(statement, params)
                            if cur.description is not None:
                                rows = await cur.fetchall()
                    return rows if fetch_last else []
        except (psycopg.OperationalError, PoolTimeout) as exc:
            await self._pool.invalidate()
            raise DatabaseUnavailableError("ingest storage is unavailable") from exc

    def _entity(self, source_id: str, entity: str) -> Any:
        source = self.sources[source_id]
        if entity not in self.contract.entities or (source.entities and entity not in source.entities):
            raise IngestError(422, "invalid_request", f"entity '{entity}' is not accepted for source {source_id}")
        return self.contract.entities[entity]

    async def _load_row(self, source_id: str, load_id: str) -> dict[str, Any]:
        rows = await self._run([("SELECT * FROM ingest.loads WHERE load_id = %s", (load_id,))])
        if not rows or rows[0]["source_id"] != source_id:
            raise IngestError(404, "not_found", "load not found")
        return rows[0]

    # ------------------------------------------------------------------ runs

    async def state(self, source_id: str) -> dict[str, Any]:
        rows = await self._run([(
            """
            SELECT max(finished_at) FILTER (WHERE kind = 'full' AND status = 'succeeded') AS last_full_success_at,
                   max(finished_at) FILTER (WHERE status = 'succeeded') AS last_success_at
            FROM ingest.runs WHERE source_id = %s
            """, (source_id,))])
        row = rows[0] if rows else {}
        return {"source_id": source_id, "contract_version": self.contract.version,
                "last_full_success_at": json_safe(row.get("last_full_success_at")),
                "last_success_at": json_safe(row.get("last_success_at"))}

    async def start_run(self, source_id: str, kind: str, connector: dict[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        # a "check" run (diagnostics) must not replace the description of the production connector
        info_update = ("last_seen_at = now()" if kind == "check" else "last_seen_at = now(), info = EXCLUDED.info")
        await self._run([
            ("UPDATE ingest.runs SET status = 'failed', finished_at = now(), error = 'abandoned' "
             "WHERE source_id = %s AND status = 'running' AND started_at < now() - make_interval(hours => %s)",
             (source_id, ABANDONED_RUN_HOURS)),
            ("INSERT INTO ingest.runs (run_id, source_id, kind, status) VALUES (%s, %s, %s, 'running')",
             (run_id, source_id, kind)),
            ("INSERT INTO ingest.connectors (source_id, last_seen_at, info) VALUES (%s, now(), %s) "
             "ON CONFLICT (source_id) DO UPDATE SET " + info_update,
             (source_id, Jsonb(connector))),
        ], fetch_last=False)
        logger.info("connector run started", extra={"source_id": source_id, "run_id": run_id, "kind": kind})
        return run_id

    async def finish_run(self, source_id: str, run_id: str, status: str, stats: dict[str, Any], error: str | None) -> None:
        await self._run([
            ("UPDATE ingest.runs SET status = %s, stats = %s, error = %s, finished_at = now() "
             "WHERE run_id = %s AND source_id = %s AND status = 'running' RETURNING run_id",
             (status, Jsonb(stats), (error or "")[:4000] or None, run_id, source_id)),
            ("UPDATE ingest.connectors SET last_seen_at = now() WHERE source_id = %s", (source_id,)),
        ], fetch_last=False)
        logger.info("connector run finished", extra={"source_id": source_id, "run_id": run_id, "status": status})

    # ------------------------------------------------------------------ loads

    async def open_load(self, source_id: str, run_id: str, entity: str, mode: str, window_from: date | None,
                        window_to: date | None) -> str:
        spec = self._entity(source_id, entity)
        if mode != spec.load:
            raise IngestError(422, "invalid_request", f"entity '{entity}' must be loaded with mode '{spec.load}'")
        if mode == "replace_window":
            if window_from is None or window_to is None or window_from >= window_to:
                raise IngestError(422, "invalid_request", "replace_window needs window_from < window_to")
            if (window_to - window_from).days > 1200:
                raise IngestError(422, "invalid_request", "window is longer than 1200 days")
        runs = await self._run([("SELECT status FROM ingest.runs WHERE run_id = %s AND source_id = %s", (run_id, source_id))])
        if not runs or runs[0]["status"] != "running":
            raise IngestError(409, "invalid_request", "run is not active")
        load_id = str(uuid.uuid4())
        await self._run([
            ("DELETE FROM ingest.staging WHERE load_id IN (SELECT load_id FROM ingest.loads WHERE source_id = %s "
             "AND status = 'open' AND created_at < now() - make_interval(hours => %s))", (source_id, STALE_LOAD_HOURS)),
            ("UPDATE ingest.loads SET status = 'failed' WHERE source_id = %s AND status = 'open' "
             "AND created_at < now() - make_interval(hours => %s)", (source_id, STALE_LOAD_HOURS)),
            ("INSERT INTO ingest.loads (load_id, run_id, source_id, entity, mode, window_from, window_to, status) "
             "VALUES (%s, %s, %s, %s, %s, %s, %s, 'open')",
             (load_id, run_id, source_id, entity, mode, window_from if mode == "replace_window" else None,
              window_to if mode == "replace_window" else None)),
        ], fetch_last=False)
        return load_id

    async def append_chunk(self, source_id: str, load_id: str, seq: int, records: list[Any]) -> int:
        if len(records) > self.max_chunk_records:
            raise IngestError(413, "invalid_request", f"chunk has more than {self.max_chunk_records} records")
        load = await self._load_row(source_id, load_id)
        if load["status"] != "open":
            raise IngestError(409, "invalid_request", "load is not open")
        spec = self._entity(source_id, load["entity"])
        problems: list[str] = []
        for index, record in enumerate(records):
            issues = self.contract.validate(load["entity"], record)
            if not issues and load["mode"] == "replace_window":
                value = record.get(spec.window_field)
                if not (load["window_from"].isoformat() <= value < load["window_to"].isoformat()):
                    issues = [f"'{spec.window_field}' {value} is outside the load window"]
            if issues:
                problems.append(f"record {index}: {'; '.join(issues[:3])}")
                if len(problems) >= 5:
                    break
        if problems:
            raise IngestError(422, "contract_violation", " | ".join(problems))

        pool = await self._pool.get()
        try:
            async with pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM ingest.staging WHERE load_id = %s AND seq = %s", (load_id, seq))
                    async with conn.cursor() as cur:
                        async with cur.copy("COPY ingest.staging (load_id, seq, record) FROM STDIN") as copy:
                            for record in records:
                                await copy.write_row((load_id, seq, Jsonb(record)))
        except (psycopg.OperationalError, PoolTimeout) as exc:
            await self._pool.invalidate()
            raise DatabaseUnavailableError("ingest storage is unavailable") from exc
        return len(records)

    async def commit(self, source_id: str, load_id: str, expected: int) -> dict[str, int]:
        load = await self._load_row(source_id, load_id)
        if load["status"] != "open":
            raise IngestError(409, "invalid_request", "load is not open")
        spec = self._entity(source_id, load["entity"])
        schema = self.sources[source_id].schema_name
        table = sql.Identifier(schema, spec.name)
        columns = list(spec.fields)
        cols = sql.SQL(", ").join(map(sql.Identifier, columns))
        x_cols = sql.SQL(", ").join(sql.SQL("x.{}").format(sql.Identifier(c)) for c in columns)
        x_keys = sql.SQL(", ").join(sql.SQL("x.{}").format(sql.Identifier(k)) for k in spec.key)
        updates = sql.SQL(", ").join(sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c))
                                     for c in [*columns, "_run_id", "_loaded_at"] if c not in spec.key)
        insert = sql.SQL(
            "INSERT INTO {table} ({cols}, _run_id, _loaded_at) "
            "SELECT {x_cols}, %(run_id)s, now() FROM ("
            "  SELECT DISTINCT ON ({x_keys}) {x_cols}, x._seq FROM ("
            "    SELECT (jsonb_populate_record(NULL::{table}, st.record)).*, st.seq AS _seq "
            "    FROM ingest.staging st WHERE st.load_id = %(load_id)s"
            "  ) x ORDER BY {x_keys}, x._seq DESC"
            ") x "
            "ON CONFLICT ({keys}) DO UPDATE SET {updates}"
        ).format(table=table, cols=cols, x_cols=x_cols, x_keys=x_keys, keys=sql.SQL(", ").join(map(sql.Identifier, spec.key)),
                 updates=updates)
        if spec.load == "replace_all":
            delete: sql.Composable = sql.SQL("DELETE FROM {}").format(table)
            delete_params: tuple[Any, ...] = ()
        else:
            delete = sql.SQL("DELETE FROM {t} WHERE {f} >= %s AND {f} < %s").format(t=table, f=sql.Identifier(spec.window_field))
            delete_params = (load["window_from"], load["window_to"])

        pool = await self._pool.get()
        try:
            async with pool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{schema}.{spec.name}",))
                        await cur.execute("SELECT status FROM ingest.loads WHERE load_id = %s FOR UPDATE", (load_id,))
                        locked = await cur.fetchone()
                        if not locked or locked["status"] != "open":
                            raise IngestError(409, "invalid_request", "load is not open")
                        await cur.execute("SELECT count(*) AS n FROM ingest.staging WHERE load_id = %s", (load_id,))
                        received = int((await cur.fetchone())["n"])
                        if received != expected:
                            raise IngestError(409, "count_mismatch", f"expected {expected} records, received {received}")
                        await cur.execute(delete, delete_params)
                        deleted = cur.rowcount
                        await cur.execute(insert, {"run_id": load["run_id"], "load_id": load_id})
                        inserted = cur.rowcount
                        await cur.execute(
                            "UPDATE ingest.loads SET status = 'committed', received = %s, inserted = %s, deleted = %s, "
                            "committed_at = now() WHERE load_id = %s", (received, inserted, deleted, load_id))
                        await cur.execute("DELETE FROM ingest.staging WHERE load_id = %s", (load_id,))
                        await cur.execute(sql.SQL("ANALYZE {}").format(table))
        except psycopg.errors.DataError as exc:
            raise IngestError(422, "contract_violation", f"records do not fit the canonical table: {exc.diag.message_primary}") from exc
        except (psycopg.OperationalError, PoolTimeout) as exc:
            await self._pool.invalidate()
            raise DatabaseUnavailableError("ingest storage is unavailable") from exc
        logger.info("load committed", extra={"source_id": source_id, "entity": spec.name, "received": received,
                                             "inserted": inserted, "deleted": deleted})
        return {"received": received, "inserted": inserted, "deleted": deleted}

    # ------------------------------------------------------------------ status for the UI

    async def status(self, source_id: str) -> dict[str, Any]:
        source = self.sources[source_id]
        connector_rows = await self._run([("SELECT last_seen_at, info FROM ingest.connectors WHERE source_id = %s", (source_id,))])
        runs = await self._run([(
            "SELECT run_id, kind, status, started_at, finished_at, stats, error FROM ingest.runs "
            "WHERE source_id = %s AND kind <> 'check' ORDER BY started_at DESC LIMIT 5", (source_id,))])
        loads = await self._run([(
            "SELECT DISTINCT ON (entity) entity, committed_at, received, window_from, window_to FROM ingest.loads "
            "WHERE source_id = %s AND status = 'committed' ORDER BY entity, committed_at DESC", (source_id,))])
        entity_names = source.entities or list(self.contract.entities)
        counts = await self._run([(
            sql.SQL(" UNION ALL ").join(
                sql.SQL("SELECT {name} AS entity, count(*) AS n FROM {table}").format(
                    name=sql.Literal(e), table=sql.Identifier(source.schema_name, e)) for e in entity_names), None)])
        by_entity = {r["entity"]: r for r in loads}
        rows_by_entity = {r["entity"]: int(r["n"]) for r in counts}
        info = connector_rows[0]["info"] if connector_rows else {}
        entities = []
        for name in entity_names:
            spec = self.contract.entities[name]
            load = by_entity.get(name, {})
            mapped = (info.get("entities") or {}).get(name, {})
            entities.append({
                "entity": name, "label": spec.label, "kind": spec.kind, "rows": rows_by_entity.get(name, 0),
                "last_loaded_at": json_safe(load.get("committed_at")),
                "window": {"from": json_safe(load.get("window_from")), "to": json_safe(load.get("window_to"))} if load.get("window_from") else None,
                "source_objects": mapped.get("objects", []), "fields": mapped.get("fields", []),
                "excluded_fields": mapped.get("excluded_fields", []),
            })
        return {
            "contract_version": self.contract.version,
            "connector": {
                "last_seen_at": json_safe(connector_rows[0]["last_seen_at"]) if connector_rows else None,
                "system": info.get("system"), "configuration": info.get("configuration"), "adapter": info.get("adapter"),
                "endpoint": info.get("endpoint"), "version": info.get("connector_version"), "read_only": info.get("read_only"),
            },
            "runs": [{"run_id": str(r["run_id"]), "kind": r["kind"], "status": r["status"],
                      "started_at": json_safe(r["started_at"]), "finished_at": json_safe(r["finished_at"]),
                      "duration_s": (r["stats"] or {}).get("duration_s"), "error": r["error"]} for r in runs],
            "entities": entities,
        }
