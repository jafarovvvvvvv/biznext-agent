#!/bin/sh
# Roles and schemas. Runs once, on first initialization of the Postgres volume.
# The agent role gets data access in 20_grants.sh after tables exist.
set -eu

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
: "${APP_DB_USER:?}" "${APP_DB_PASSWORD:?}"
: "${AGENT_DB_USER:?}" "${AGENT_DB_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_user="$POSTGRES_USER" \
  -v db_name="$POSTGRES_DB" \
  -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" \
  -v agent_user="$AGENT_DB_USER" -v agent_password="$AGENT_DB_PASSWORD" <<'SQL'
-- application user: owns schema app (conversation history, agent traces)
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

-- agent user: read-only access to business data only
CREATE ROLE :"agent_user" LOGIN PASSWORD :'agent_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT CONNECTION LIMIT 20;

-- nobody gets implicit rights (CONNECT, TEMP) through PUBLIC
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO :"app_user", :"agent_user";

REVOKE ALL ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA demo AUTHORIZATION :"owner_user";
COMMENT ON SCHEMA demo IS 'Бизнес-данные предприятия (demo, synthetic data). Агент читает только эту схему.';

CREATE SCHEMA app AUTHORIZATION :"app_user";
COMMENT ON SCHEMA app IS 'Служебные данные BizNext Agent: история диалогов и Agent Trace.';
SQL

echo "roles and schemas created"
