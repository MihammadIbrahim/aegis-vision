# Backend

Status (2026-10-29): only **`services/core_api`** (FastAPI + PostgreSQL 16) has working code. The other
services, `docker-compose.yml`, the Dockerfiles and `k8s/` are empty placeholders.

## Documentation

- **Overall:** `../../FYP_Documentation/` (requirements, page specifications, architecture).
- **Backend:** `docs/` (decisions, implementation log, module guides, API index, permission matrix).

## Run the tests (from `services/core_api`)

```
TEST_POSTGRES_OWNER_URL=postgresql://<owner>@127.0.0.1:<port>/postgres \
TEST_POSTGRES_APP_USER=cctv_app TEST_POSTGRES_APP_PASSWORD=<app password> \
TEST_POSTGRES_PLATFORM_USER=cctv_platform TEST_POSTGRES_PLATFORM_PASSWORD=<platform password> \
../../.venv/bin/python -B -m pytest -p no:cacheprovider -q
```

- Integration tests need a PostgreSQL server whose owner role may create databases. The roles are
  made by `docker/postgres/init/02-create-roles.sh`.
- Without `TEST_POSTGRES_OWNER_URL`, the database tests are skipped.
- **Migrations:** `alembic upgrade head`, with `MIGRATION_DATABASE_URL`, `APP_DB_USER` and
  `PLATFORM_DB_USER` set.
- **Configuration:** `.env.example`. Put real secrets only in your own `.env`.

Docker-based setup is not available yet: the Compose file is empty.
