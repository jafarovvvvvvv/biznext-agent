#!/bin/sh
# Database-level read-only guarantee for the agent (independent of the SQL validator).
set -eu

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}" "${APP_DB_USER:?}" "${AGENT_DB_USER:?}"
STATEMENT_TIMEOUT_MS="${SQL_STATEMENT_TIMEOUT_MS:-15000}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_user="$POSTGRES_USER" \
  -v app_user="$APP_DB_USER" \
  -v agent_user="$AGENT_DB_USER" \
  -v statement_timeout="$STATEMENT_TIMEOUT_MS" <<'SQL'
GRANT USAGE ON SCHEMA demo TO :"agent_user";
GRANT SELECT ON ALL TABLES IN SCHEMA demo TO :"agent_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_user" IN SCHEMA demo GRANT SELECT ON TABLES TO :"agent_user";

-- the application user has no rights on business data; the agent has none on app
REVOKE ALL ON SCHEMA demo FROM :"app_user";
REVOKE ALL ON SCHEMA app FROM :"agent_user";

ALTER ROLE :"agent_user" SET default_transaction_read_only = on;
ALTER ROLE :"agent_user" SET statement_timeout = :'statement_timeout';
ALTER ROLE :"agent_user" SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE :"agent_user" SET search_path = demo;
ALTER ROLE :"app_user" SET search_path = app;
SQL

echo "grants applied"
