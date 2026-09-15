"""Idempotent database migration for data connectors (§56). Runs on every `docker compose up` as `migrate`.

Creates, under the owner role:
  * role INGEST_DB_USER — writes only canonical source schemas and the `ingest` service schema;
  * schema `ingest` — load staging, sync runs and connector status (no access for the agent);
  * one schema per canonical data source from semantic/sources.yaml with tables generated from the
    canonical contract (contracts/canonical/v1.yaml). The agent role gets SELECT only.

Existing volumes created before §56 are upgraded in place; nothing is dropped.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg import sql

TYPES = {"string": "text", "integer": "bigint", "decimal": "numeric(18,4)", "date": "date", "boolean": "boolean"}

INGEST_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS ingest;
COMMENT ON SCHEMA ingest IS 'Служебная схема коннекторов: загрузки, синхронизации, статус. Агенту недоступна.';
CREATE TABLE IF NOT EXISTS ingest.connectors (
    source_id    text PRIMARY KEY,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    info         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS ingest.runs (
    run_id      uuid PRIMARY KEY,
    source_id   text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('full', 'incremental', 'check')),
    status      text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    stats       jsonb NOT NULL DEFAULT '{}'::jsonb,
    error       text
);
CREATE INDEX IF NOT EXISTS runs_source_idx ON ingest.runs (source_id, started_at DESC);
CREATE TABLE IF NOT EXISTS ingest.loads (
    load_id      uuid PRIMARY KEY,
    run_id       uuid REFERENCES ingest.runs (run_id) ON DELETE CASCADE,
    source_id    text NOT NULL,
    entity       text NOT NULL,
    mode         text NOT NULL CHECK (mode IN ('replace_all', 'replace_window')),
    window_from  date,
    window_to    date,
    status       text NOT NULL CHECK (status IN ('open', 'committed', 'failed')),
    received     integer NOT NULL DEFAULT 0,
    inserted     integer,
    deleted      integer,
    created_at   timestamptz NOT NULL DEFAULT now(),
    committed_at timestamptz
);
CREATE INDEX IF NOT EXISTS loads_source_entity_idx ON ingest.loads (source_id, entity, committed_at DESC);
CREATE UNLOGGED TABLE IF NOT EXISTS ingest.staging (
    load_id uuid NOT NULL,
    seq     integer NOT NULL,
    record  jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS staging_load_idx ON ingest.staging (load_id);
"""


def wait_for_database(url: str) -> psycopg.Connection:
    for attempt in range(30):
        try:
            return psycopg.connect(url, autocommit=True)
        except psycopg.OperationalError:
            if attempt == 29:
                raise
            time.sleep(2)
    raise RuntimeError("unreachable")


def ensure_role(cur: psycopg.Cursor, name: str, password: str, database: str) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    if cur.fetchone() is None:
        cur.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT "
                            "CONNECTION LIMIT 10").format(sql.Identifier(name)))
        print(f"  role {name} created")
    cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(name), sql.Literal(password)))
    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(name)))
    cur.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '300s'").format(sql.Identifier(name)))


def table_ddl(schema: str, entity: str, spec: dict[str, Any]) -> list[sql.Composable]:
    fields: dict[str, Any] = spec["fields"]
    table = sql.Identifier(schema, entity)
    columns = [sql.SQL("{} {}{}").format(sql.Identifier(name), sql.SQL(TYPES[f["type"]]),
                                         sql.SQL(" NOT NULL") if name in spec["key"] else sql.SQL(""))
               for name, f in fields.items()]
    columns += [sql.SQL("_run_id uuid"), sql.SQL("_loaded_at timestamptz NOT NULL DEFAULT now()")]
    statements: list[sql.Composable] = [
        sql.SQL("CREATE TABLE IF NOT EXISTS {} ({}, PRIMARY KEY ({}))").format(
            table, sql.SQL(", ").join(columns), sql.SQL(", ").join(map(sql.Identifier, spec["key"])))]
    for name, f in fields.items():
        statements.append(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}").format(
            table, sql.Identifier(name), sql.SQL(TYPES[f["type"]])))
        description = (f.get("description") or {}).get("ru")
        if description:
            statements.append(sql.SQL("COMMENT ON COLUMN {} IS {}").format(
                sql.Identifier(schema, entity, name), sql.Literal(description)))
        if name != spec["key"][0] and (name == spec.get("window_field") or f.get("ref") or name == "document_id"):
            statements.append(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ({})").format(
                sql.Identifier(f"{entity}_{name}_idx"), table, sql.Identifier(name)))
    description = (spec.get("description") or {}).get("ru")
    if description:
        statements.append(sql.SQL("COMMENT ON TABLE {} IS {}").format(table, sql.Literal(description)))
    return statements


def migrate(url: str, contract: dict[str, Any], sources: dict[str, Any]) -> None:
    owner = psycopg.conninfo.conninfo_to_dict(url).get("user") or os.environ["POSTGRES_USER"]
    database = os.environ.get("POSTGRES_DB") or psycopg.conninfo.conninfo_to_dict(url).get("dbname") or "biznext"
    ingest_user = os.environ.get("INGEST_DB_USER") or "biznext_ingest"
    ingest_password = os.environ.get("INGEST_DB_PASSWORD", "")
    agent_user = os.environ.get("AGENT_DB_USER") or "agent_readonly"
    app_user = os.environ.get("APP_DB_USER") or "biznext_app"
    if len(ingest_password) < 12:
        raise SystemExit("INGEST_DB_PASSWORD must be set (>= 12 characters); run scripts/init_env.py")

    with wait_for_database(url) as conn, conn.cursor() as cur:
        ensure_role(cur, ingest_user, ingest_password, database)
        ingest = sql.Identifier(ingest_user)
        cur.execute(INGEST_SCHEMA_SQL)
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA ingest TO {}").format(ingest))
        cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ingest TO {}").format(ingest))
        cur.execute(sql.SQL("REVOKE ALL ON SCHEMA ingest FROM {}, {}").format(sql.Identifier(agent_user), sql.Identifier(app_user)))
        # the ingest role never touches business data of other sources or application storage
        cur.execute(sql.SQL("REVOKE ALL ON SCHEMA demo FROM {}").format(ingest))
        cur.execute(sql.SQL("REVOKE ALL ON SCHEMA app FROM {}").format(ingest))

        entities: dict[str, Any] = contract["entities"]
        for source_id, source in sources.items():
            if source.get("storage") != "canonical":
                continue
            schema = source["schema"]
            wanted = source.get("entities") or list(entities)
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(sql.Identifier(schema), sql.Identifier(owner)))
            cur.execute(sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
                sql.Identifier(schema), sql.Literal(f"Канонические данные источника {source_id} (контракт v{contract['version']}); "
                                                    "пишет только BizNext Connector через ingest API")))
            for entity in wanted:
                for statement in table_ddl(schema, entity, entities[entity]):
                    cur.execute(statement)
            ident = sql.Identifier(schema)
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}, {}").format(ident, sql.Identifier(agent_user), ingest))
            cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(ident, sql.Identifier(agent_user)))
            cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE, MAINTAIN ON ALL TABLES IN SCHEMA {} TO {}").format(ident, ingest))
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(
                sql.Identifier(owner), ident, sql.Identifier(agent_user)))
            cur.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(ident, sql.Identifier(app_user)))
            print(f"  canonical schema {schema}: {len(wanted)} entities")
        cur.execute("ANALYZE ingest.runs")
    print("migrations applied")


def main() -> int:
    url = os.environ.get("SEED_DATABASE_URL")
    if not url:
        print("SEED_DATABASE_URL is not set", file=sys.stderr)
        return 2
    contract_path = Path(os.environ.get("CONTRACT_PATH") or "/contracts/canonical/v1.yaml")
    sources_path = Path(os.environ.get("SOURCES_PATH") or "/semantic/sources.yaml")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    sources = yaml.safe_load(sources_path.read_text(encoding="utf-8"))["sources"]
    migrate(url, contract, sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
