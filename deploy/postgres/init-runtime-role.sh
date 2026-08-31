#!/bin/sh
set -eu

if [ -z "${POSTGRES_RUNTIME_PASSWORD:-}" ]; then
  echo "POSTGRES_RUNTIME_PASSWORD is required" >&2
  exit 2
fi

psql --set ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set database_name="$POSTGRES_DB" \
  --set runtime_password="$POSTGRES_RUNTIME_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE knowledge_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
  :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'knowledge_app')
\gexec
GRANT CONNECT ON DATABASE :"database_name" TO knowledge_app;
GRANT USAGE ON SCHEMA public TO knowledge_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO knowledge_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO knowledge_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO knowledge_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO knowledge_app;
SQL
