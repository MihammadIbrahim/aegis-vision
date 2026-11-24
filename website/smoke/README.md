# Live smoke check (Personnel Management)

`personnel_smoke.py` checks the web app's contract with the real Core API **through the Vite dev
server's `/api` proxy**, using the same URLs, methods and bodies as the pages. It covers:
- real organization registration, and Admin and Supervisor sign-in, with codes from the development
  email outbox;
- registering a person and field errors;
- what the Supervisor can and cannot see or do;
- granting and revoking restricted-area access;
- the template, upload, mapping, preview, confirm and history steps of an import;
- deactivating a person.

It runs on the Python standard library only.

## Safety

- **Run it only against an isolated scratch database, never a shared, development or production
  one.** Each run creates a new organization, and nothing is changed or deleted.
- The script refuses non-local servers, and refuses to start without a development outbox
  directory.

## Steps

1. **Database:** create a new, empty PostgreSQL database for the run and migrate it:
   `MIGRATION_DATABASE_URL=… alembic upgrade head`, run in `backend/services/core_api`.
2. **Core API:** start it on that database with development email:
   ```bash
   APP_ENV=development EMAIL_DELIVERY_MODE=dev_outbox DEV_OUTBOX_DIR=/tmp/…/outbox \
   OTP_RESEND_COOLDOWN_SECONDS=10 DATABASE_URL=… PLATFORM_DATABASE_URL=… MIGRATION_DATABASE_URL=… \
   JWT_SECRET_KEY=<a local test value> CCTV_STORAGE_ROOT=/tmp/…/storage \
   uvicorn src.main:create_app --factory --port 8765
   ```
3. **Web app:** `VITE_API_TARGET=http://127.0.0.1:8765 npx vite --port 5179`.
4. **Run:** `python3 smoke/personnel_smoke.py --web http://127.0.0.1:5179 --outbox /tmp/…/outbox`.

The exit code is 0 when every check passes. Run it before and after each refactoring step that
touches Personnel Management.

## Alerts, manual reports and notifications (`operations_smoke.py`)

Same setup, plus `DETECTION_INGEST_TOKEN=<a local test value>` for the API, because alerts are
created through the real detection intake. It creates two organizations, a Supervisor and two Guards,
a location and two cameras, then checks the alert list and filters, details, history, Guard actions,
chat, escalation, dismissal, resolution, manual reports with a photo, review, notifications, and that
the second organization sees none of it.

```bash
python3 smoke/operations_smoke.py --web http://127.0.0.1:5179 --outbox /tmp/…/outbox --token <the same value>
```

It leaves the data in place and prints the sign-in details, so the pages can be looked at afterwards.

## Buildings & Locations and Cameras (`site_smoke.py`)

Same setup as the operations check (it needs `DETECTION_INGEST_TOKEN`, to show that a camera with alerts
cannot be removed). It creates two organizations, a Supervisor and a Guard, then checks every location and
camera request the Phase 7 pages send: Admin-only building changes (Supervisor 403), floors and areas with
boundaries for both roles, camera create/edit/delete with and without a location, refusal of stream URLs
with credentials, search and filters, linked cameras and Guards, refusals for locations still in use, Guard
access refused, and that the second organization sees none of it.

```bash
python3 smoke/site_smoke.py --web http://127.0.0.1:5179 --outbox /tmp/…/outbox --token <the same value>
```

## Staff Management (`staff_smoke.py`)

Same setup (no ingest token needed). It runs the four-step Guard creation exactly as the wizard sends it,
then checks: Supervisors refused creation and the Supervisor list; Standby → Assigned → Standby through
`/locations/guard-assignments`; Guard details and password set by a Supervisor (own password required,
taken username refused, the Guard's earlier session ended); Admin notifications for each Supervisor change;
deactivation and reactivation by the Admin only; and that a second organization sees nothing.

```bash
python3 smoke/staff_smoke.py --web http://127.0.0.1:5179 --outbox /tmp/…/outbox
```

Since decision D-017 only Admins create Guards, so `operations_smoke.py` and `site_smoke.py` create their
Guards as the Admin.

Since decision D-018, Guard sign-in sends `operational_mode` (`STANDBY` or `STATIONED`, matching the Guard's
assignment) with the Guard ID, email and password and again with the code. `staff_smoke.py` also checks the
forced sign-out when a post is set or cleared, the mode mismatch at sign-in and forgot password with a mode;
`operations_smoke.py` checks that Guard 1's Standby session ends when its post is set.

## Registration, campus requests, sign-in and forgot password (`auth_smoke.py`)

Same setup (no ingest token needed). It registers an organization with a first-campus name (decision D-021) and
checks both returned IDs; refuses campus requests whose organization name (capitals) or Organization ID does not
match, and a campus name already used; verifies a request, checks "approval pending" at sign-in, approves it as the
Main Admin and signs the new campus Admin in; then resets the Main Admin's password by Organization ID + email
(same answer for unknown accounts, one-time token, earlier session signed out, old password refused).

```bash
python3 smoke/auth_smoke.py --web http://127.0.0.1:5179 --outbox /tmp/…/outbox
```

## Live monitoring, recordings, retention and evidence (`video_smoke.py`, decision D-023)

Same setup, plus for the API `DETECTION_INGEST_TOKEN`, `INTERNAL_SERVICE_TOKEN` and a **scratch**
`CCTV_STORAGE_ROOT`, and `ffmpeg` on the `PATH`. Unlike the other checks, this one also starts the real backend
services against the same API: `video_ingestion` (a generated video plays as one camera through
`INGEST_FILE_SOURCES`; a second camera points at a closed local port; a third is ONVIF) and one pass of
`storage_retention_daemon`. It checks statuses, the offline notification, live frames, the live-view audit
call, the connection test, segments with purge times and an AI marker, playback, isolation from a second
organization, evidence from an alert and from playback, metadata edits, the password-confirmed retention
change by a Supervisor and an Admin, campus usage by tier, Admin-only evidence deletion, and that the
1-minute retention purge deletes the routine files but never the evidence files. It takes about three minutes
(it waits for the newest segment to expire).

```bash
python3 smoke/video_smoke.py --web http://127.0.0.1:5179 --outbox /tmp/…/outbox --token <detection token> \
    --service-token <service token> --storage /tmp/…/storage --services ../backend/services \
    --python ../backend/.venv/bin/python [--summary /tmp/…/summary.json]
```

## Real-time messaging and voice calls across the Guard app and the web app (`realtime_comms_smoke.py`, decision D-026)

Same setup, plus for the API `DETECTION_INGEST_TOKEN`, `VOICE_CALL_PROVIDER=livekit`, `LIVEKIT_URL`,
`LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` (**placeholder values are enough**: tokens are signed and the call lifecycle
is real, but nobody connects to a LiveKit server, so no audio is tested; a closed local port such as `ws://127.0.0.1:9`
for `LIVEKIT_URL` with `LIVEKIT_REQUEST_TIMEOUT_SECONDS=1` keeps the best-effort room close fast). For the Super Admin
checks, seed one on the scratch database (`SUPER_ADMIN_USERNAME=… SUPER_ADMIN_PASSWORD=… python -m
src.entrypoints.seed_super_admin`, run in `backend/services/core_api`).

It signs in an Admin, a Supervisor, two Guards (as the Guard app does) and the Admin of a second, approved campus, opens
five WebSockets **through the Vite proxy** (so it also proves `ws: true`), and checks: new alerts and Guard actions
(acknowledge, Additional Guards, I'm Responding) reach the web staff; alert chat and case chat both ways; case
invitation, note and status signals; a Guard-started alert call ringing the web staff with the target's title and
location, joined by the Supervisor, declined by the Admin and ended for everyone (the Guard hears `ENDED`); a
Supervisor-started case call joined by the Guard and the Admin (a second start joins), which the Guard cannot end and
the Admin can; audio-only LiveKit tokens for the right room (signature checked with `--livekit-secret`); the other
campus receives no signal and gets `404`; Super Admin refused over REST (`403`) and on the socket (`4403`); a bad token
refused (`4401`); a Guard's assignment change ends only that Guard's socket.

```bash
python3 smoke/realtime_comms_smoke.py --web http://127.0.0.1:5179 --outbox /tmp/…/outbox --token <detection token> \
    --livekit-secret <the API's LIVEKIT_API_SECRET> --super-admin <username> --super-admin-password <password>
```


## AI Investigation & Security Analytics Chatbot (`chatbot_smoke.py`, decision D-027)

Same setup, plus for the API `DETECTION_INGEST_TOKEN`, `INTERNAL_SERVICE_TOKEN` (to check the purge route) and
`AI_LLM_PROVIDER=deterministic` (the default; no model, no network). Seed a Super Admin for the 403 check as in the
real-time check. It creates an organization with a Supervisor, two Guards, a place and a camera, three alerts through
the detection intake, a Guard acknowledgement, an "Additional Guards" assessment and a chat message, two manual
reports and a case opened from the weapon alert, plus a second, approved campus. Then it checks the chatbot's answers
against **independent REST counts** (`GET /alerts`, `GET /manual-reports`): trends, hotspots and a follow-up ("how many
of those are still unresolved…"), Guard response, manual reports, "Investigate with AI" (timeline, related events,
gaps), missing data, a refused state-changing request (the alert is unchanged), no accusation, the Supervisor's
excluded data, chat privacy, campus isolation, Guard and Super Admin refusals, `AI_ASSISTANT` audit records (a
Supervisor sees only their own), deleting a chat and the retention purge route.

```bash
python3 smoke/chatbot_smoke.py --web http://127.0.0.1:5179 --outbox /tmp/…/outbox --token <detection token> \
    --service-token <INTERNAL_SERVICE_TOKEN> --super-admin <username> --super-admin-password <password>
```

## AI feature controls (`ai_controls_smoke.py`, decision D-028)

Same setup, plus `INTERNAL_SERVICE_TOKEN=<a local test value>` (the check reads what a vision worker is told to run)
and a seeded Super Admin (as above). It creates two organizations, a Supervisor, a second campus and several cameras,
then checks: a new camera runs nothing; AI features chosen with the camera; `ai_modules` on `GET /cameras`; search +
bulk ADD / REMOVE / SET (all or nothing, another campus's camera refused, wrong password refused); the Admin's
Supervisor visibility (hidden module refused for the Supervisor and kept by the Supervisor's changes); the Super
Admin's block / unblock (tenant token 401, wrong password 403, immediate stop for this organization only, 403 on
re-enable, no automatic re-enable after unblocking) and the platform and organization audit records.

```bash
python3 smoke/ai_controls_smoke.py --web http://127.0.0.1:5181 --outbox /tmp/…/outbox \
    --service-token <INTERNAL_SERVICE_TOKEN> --super-admin <username> --super-admin-password <password>
```
