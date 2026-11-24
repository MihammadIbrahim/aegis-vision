# storage_retention_daemon

Every `RETENTION_INTERVAL_SECONDS` (300 by default) — decision D-023:

1. **Purge expired routine recordings.** The Core API decides what has expired
   (`GET /internal/recordings/expired`: each campus's retention in minutes, hours or days, or the server
   default). The daemon unlinks those files and reports them (`POST /internal/recordings/purge`); empty date and
   camera folders are removed.
2. **Protect the evidence vault.** A path is deleted only if it resolves inside `<root>/recordings/`. Anything
   else — absolute paths, `..`, a path into `evidence/` — is refused and counted, never deleted. The daemon never
   lists or opens `<root>/evidence/`; evidence is deleted only through the API, by an Admin, with their password.
3. **Drop stale CV spool frames** (`frames/`, older than `RETENTION_SPOOL_MAX_AGE_SECONDS`).
4. **Measure the disk** and report it (`POST /internal/storage/report`). The API compares it with
   `STORAGE_WARNING_PERCENT` / `STORAGE_CRITICAL_PERCENT` (80 / 90) and notifies the Admins and Supervisors of every
   active campus once per level change (again after 24 hours at the same level).
5. **Expire Re-ID observations** (decision D-024): `POST /internal/reid/purge-expired` deletes person observations
   older than their campus's recording retention, so appearance data never outlives the footage it came from.
6. **Purge inactive AI chatbot sessions** (decision D-027): `POST /internal/assistant/purge-expired` deletes chat
   sessions (and their messages) not used for longer than the API's `AI_CHAT_RETENTION_DAYS` (30 by default).

## Run

```bash
cd backend/services/storage_retention_daemon
RETENTION_API_BASE_URL=http://127.0.0.1:8000 RETENTION_SERVICE_TOKEN=<INTERNAL_SERVICE_TOKEN of the API> \
RETENTION_STORAGE_ROOT=/mnt/cctv_storage python -m src.main          # --once for a single pass
```

| Setting | Default | Meaning |
|---|---|---|
| `RETENTION_API_BASE_URL` | `http://127.0.0.1:8000` | Core API |
| `RETENTION_SERVICE_TOKEN` | — (required) | the API's `INTERNAL_SERVICE_TOKEN` |
| `RETENTION_STORAGE_ROOT` | `/mnt/cctv_storage` | the API's `CCTV_STORAGE_ROOT` |
| `RETENTION_INTERVAL_SECONDS` | 300 | pass interval |
| `RETENTION_BATCH_SIZE` | 500 | expired segments per request |
| `RETENTION_SPOOL_MAX_AGE_SECONDS` | 600 | stale CV frames |

`RecordingsCleaner.sweep_orphans` (recording files the index no longer knows, e.g. after a crash between writing
and registering) exists and is tested, but the pass does not call it: the daemon has no copy of the index, and
it never removes a file without one.

## Tests

`python -m pytest -q` (4 tests): expiry in minutes, hours and days through a fake of the API's rule, refusal of
traversal / absolute / evidence paths with the evidence file intact, missing files, the spool and the disk
measurement.
