#!/usr/bin/env bash
# Runs once, when the Postgres container's data volume is first created.
#
# Creates the restricted login roles the services connect with. None of them is a
# superuser or can bypass Row-Level Security. Migrations run as the owner
# (POSTGRES_USER) and grant each role only the table rights it needs.
#
#   APP_DB_USER       tenant role used by core_api for organization users
#   PLATFORM_DB_USER  platform role used by core_api for Super Admin routes only
set -euo pipefail
: "${APP_DB_USER:?APP_DB_USER must be set}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"
: "${PLATFORM_DB_USER:?PLATFORM_DB_USER must be set}"
: "${PLATFORM_DB_PASSWORD:?PLATFORM_DB_PASSWORD must be set}"

create_role() {
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
       -v role_name="$1" -v role_password="$2" <<'SQL'
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'role_name', :'role_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role_name') \gexec
SQL
}

create_role "$APP_DB_USER" "$APP_DB_PASSWORD"
create_role "$PLATFORM_DB_USER" "$PLATFORM_DB_PASSWORD"
