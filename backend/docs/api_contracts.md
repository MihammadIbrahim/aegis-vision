# API contracts (as built)

All routes are under `/api/v1`, generated from the Core API's OpenAPI schema on 2026-11-01
(148 operations); Staff Management added 6 on 2026-11-11 (154 operations; decision D-017); the Platform Super
Admin management backend added 12 on 2026-11-12 (166 operations; decision D-019; its full contract is the SRS
section of `FYP_Documentation/FYP_Final_Super_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt`); campus requests
for existing organizations added 3 on 2026-11-12 (169 operations; decision D-020, migration 0021); decision D-021
(2026-11-13, migration 0022) added the required `campus_name` to registration. Request and response bodies: see the live schema at `/docs` when the API runs, and
the module guides in this folder. Errors use `{"detail", "code"?, "retry_after_seconds"?}`.

Alerts, alert chat, manual reports, notifications and the detection intake were added on 2026-10-31
(decision D-013; bodies in the live schema). Personnel Management, the Excel/CSV personnel import and
restricted-area access were added on 2026-11-01 (40 operations, decision D-016; guide and
contracts: `personnel_management.md`). Staff Management was added on 2026-11-11 (decision D-017): `GET /staff` (query `role` GUARD or
SUPERVISOR, `status` ACTIVE or LEFT, `category` STANDBY or ASSIGNED, `q`, `limit` 1–200, `offset`) and
`GET /staff/{user_id}` return `{id, display_id, role, status, full_name, username, email, phone_number,
created_at, category, location}`; `PUT /staff/guards/{user_id}` takes `{full_name, username, phone_number,
current_password}`; `PUT /staff/guards/{user_id}/password` takes `{new_password, current_password}` (204);
`POST /staff/{user_id}/deactivate|reactivate` take `{current_password}` (Admins only). Guard creation
(`/guards…`) is Admin-only since then. Notifications carry `subject_user_id` (the Guard of a staff
notification). Since D-018 (2026-11-11) the staff `category` values are `STANDBY` and
`STATIONED`; `POST /auth/guard/login` takes `{operational_mode, guard_id, email, password}` and
`/auth/guard/login/{login_id}/verify` takes `{otp, operational_mode}` and returns `{access_token, token_type,
expires_in, operational_mode, dashboard}`; `GET /guard/dashboard` adds `operational_mode` and `dashboard`;
`POST /auth/password-reset` with `guard_id` also requires `operational_mode`. A Guard token issued before the
Guard's last assignment change answers `401` with code `ASSIGNMENT_CHANGED`. Their error bodies may also carry `errors`: per-field
problems, never with the submitted values. Contracts for modules that are **not built** (incidents,
recordings, evidence, AI settings, analytics, audit-log viewing) are **not defined yet**; requirements
Section 22 lists them as open.

| Method | Path |
|---|---|
| GET | `/alerts` |
| GET | `/alerts/{alert_id}` |
| POST | `/alerts/{alert_id}/acknowledge` |
| POST | `/alerts/{alert_id}/assessment` |
| POST | `/alerts/{alert_id}/dismiss` |
| POST | `/alerts/{alert_id}/escalate` |
| GET | `/alerts/{alert_id}/history` |
| GET | `/alerts/{alert_id}/messages` |
| POST | `/alerts/{alert_id}/messages` |
| POST | `/alerts/{alert_id}/resolve` |
| POST | `/alerts/{alert_id}/responding` |
| POST | `/auth/guard/login` |
| POST | `/auth/guard/login/{login_id}/resend-otp` |
| POST | `/auth/guard/login/{login_id}/verify` |
| GET | `/auth/me` |
| POST | `/auth/organization-admin/login` |
| POST | `/auth/organization-admin/login/{login_id}/resend-otp` |
| POST | `/auth/organization-admin/login/{login_id}/verify` |
| POST | `/auth/campus-requests` |
| POST | `/auth/campus-requests/{application_id}/resend-otp` |
| POST | `/auth/campus-requests/{application_id}/verify` |
| POST | `/auth/organization-registrations` |
| POST | `/auth/organization-registrations/{registration_id}/resend-otp` |
| POST | `/auth/organization-registrations/{registration_id}/verify` |
| POST | `/auth/password-reset` |
| POST | `/auth/password-reset/{reset_id}/complete` |
| POST | `/auth/password-reset/{reset_id}/resend-otp` |
| POST | `/auth/password-reset/{reset_id}/verify` |
| GET | `/cameras` |
| POST | `/cameras` |
| DELETE | `/cameras/{camera_id}` |
| GET | `/cameras/{camera_id}` |
| PUT | `/cameras/{camera_id}` |
| GET | `/campus-requests` |
| POST | `/campus-requests/{request_id}/approve` |
| POST | `/campus-requests/{request_id}/reject` |
| GET | `/guard/dashboard` |
| GET | `/guard/location` |
| GET | `/guard/locations` |
| GET | `/guard/manual-reports` |
| POST | `/guard/manual-reports` |
| GET | `/guard/profile` |
| POST | `/guard/profile/changes` |
| DELETE | `/guard/profile/changes/{change_id}` |
| POST | `/guard/profile/changes/{change_id}/resend-otp` |
| POST | `/guard/profile/changes/{change_id}/verify` |
| POST | `/guard/sync` |
| POST | `/guards` |
| POST | `/guards/authorization` |
| POST | `/guards/authorization/{authorization_id}/resend-otp` |
| POST | `/guards/authorization/{authorization_id}/verify` |
| POST | `/guards/creations/{creation_id}/resend-otp` |
| POST | `/guards/creations/{creation_id}/verify` |
| POST | `/internal/detections` |
| GET | `/locations` |
| DELETE | `/locations/areas/{area_id}` |
| PUT | `/locations/areas/{area_id}` |
| POST | `/locations/buildings` |
| DELETE | `/locations/buildings/{building_id}` |
| PUT | `/locations/buildings/{building_id}` |
| POST | `/locations/buildings/{building_id}/areas` |
| POST | `/locations/buildings/{building_id}/floors` |
| GET | `/locations/camera-links` |
| DELETE | `/locations/camera-links/{camera_id}` |
| PUT | `/locations/camera-links/{camera_id}` |
| DELETE | `/locations/floors/{floor_id}` |
| PUT | `/locations/floors/{floor_id}` |
| GET | `/locations/guard-assignments` |
| DELETE | `/locations/guard-assignments/{guard_id}` |
| PUT | `/locations/guard-assignments/{guard_id}` |
| GET | `/locations/{level}/{location_id}/linked` |
| GET | `/manual-reports` |
| GET | `/manual-reports/{report_id}` |
| GET | `/manual-reports/{report_id}/photo` |
| POST | `/manual-reports/{report_id}/review` |
| GET | `/notifications` |
| POST | `/notifications/read-all` |
| POST | `/notifications/{notification_id}/read` |
| GET | `/organization-admin/account/deletion` |
| POST | `/organization-admin/account/deletion` |
| GET | `/organization-admin/account/deletion/{request_id}/export` |
| POST | `/organization-admin/account/deletion/{request_id}/resend-otp` |
| POST | `/organization-admin/account/deletion/{request_id}/verify` |
| POST | `/organization-admin/account/ownership-transfer` |
| DELETE | `/organization-admin/account/ownership-transfer/{transfer_id}` |
| POST | `/organization-admin/account/ownership-transfer/{transfer_id}/resend-otp` |
| POST | `/organization-admin/account/ownership-transfer/{transfer_id}/verify` |
| GET | `/organization-admin/dashboard` |
| GET | `/organization-admin/ownership-transfers/history` |
| GET | `/organization-admin/ownership-transfers/incoming` |
| POST | `/organization-admin/ownership-transfers/{transfer_id}/accept` |
| POST | `/organization-admin/ownership-transfers/{transfer_id}/reject` |
| POST | `/organization-admin/supervisors` |
| POST | `/organization-admin/supervisors/authorization` |
| POST | `/organization-admin/supervisors/authorization/{authorization_id}/resend-otp` |
| POST | `/organization-admin/supervisors/authorization/{authorization_id}/verify` |
| POST | `/organization-admin/supervisors/creations/{creation_id}/resend-otp` |
| POST | `/organization-admin/supervisors/creations/{creation_id}/verify` |
| GET | `/organization/admin-directory` |
| GET | `/personnel` |
| POST | `/personnel` |
| GET | `/personnel/categories` |
| POST | `/personnel/categories` |
| PUT | `/personnel/categories/{category_id}` |
| GET | `/personnel/custom-fields` |
| POST | `/personnel/custom-fields` |
| PUT | `/personnel/custom-fields/{field_id}` |
| GET | `/personnel/fields` |
| GET | `/personnel/{personnel_id}` |
| PUT | `/personnel/{personnel_id}` |
| POST | `/personnel/{personnel_id}/deactivate` |
| GET | `/personnel/{personnel_id}/history` |
| DELETE | `/personnel/{personnel_id}/photo` |
| GET | `/personnel/{personnel_id}/photo` |
| PUT | `/personnel/{personnel_id}/photo` |
| POST | `/personnel/{personnel_id}/reactivate` |
| GET | `/personnel-imports` |
| POST | `/personnel-imports` |
| GET | `/personnel-imports/mapping-profiles` |
| DELETE | `/personnel-imports/mapping-profiles/{profile_id}` |
| GET | `/personnel-imports/template.xlsx` |
| DELETE | `/personnel-imports/{batch_id}` |
| GET | `/personnel-imports/{batch_id}` |
| GET | `/personnel-imports/{batch_id}/annotated-workbook` |
| POST | `/personnel-imports/{batch_id}/confirm` |
| GET | `/personnel-imports/{batch_id}/issues` |
| PUT | `/personnel-imports/{batch_id}/mapping` |
| GET | `/personnel-imports/{batch_id}/preview` |
| POST | `/personnel-imports/{batch_id}/rollback` |
| GET | `/platform/ai-settings` |
| PUT | `/platform/ai-settings` |
| GET | `/platform/audit-logs` |
| GET | `/platform/audit-logs/actions` |
| GET | `/platform/audit-logs/export` |
| POST | `/platform/auth/login` |
| GET | `/platform/deleted-organizations` |
| GET | `/platform/health` |
| GET | `/platform/me` |
| POST | `/platform/me/password` |
| POST | `/platform/me/username` |
| GET | `/platform/organizations` |
| GET | `/platform/organizations/export` |
| GET | `/platform/organizations/{organization_id}` |
| PUT | `/platform/organizations/{organization_id}/main-admin` |
| POST | `/platform/organizations/{organization_id}/status` |
| GET | `/platform/overview` |
| POST | `/platform/recovery/credentials` |
| GET | `/restricted-access/areas` |
| DELETE | `/restricted-access/areas/{area_id}` |
| PUT | `/restricted-access/areas/{area_id}` |
| GET | `/restricted-access/areas/{area_id}/grants` |
| GET | `/restricted-access/check` |
| POST | `/restricted-access/grants` |
| PUT | `/restricted-access/grants/{grant_id}` |
| GET | `/restricted-access/grants/{grant_id}/history` |
| POST | `/restricted-access/grants/{grant_id}/revoke` |
| GET | `/restricted-access/personnel/{personnel_id}/grants` |
| GET | `/staff` |
| GET | `/staff/{user_id}` |
| POST | `/staff/{user_id}/deactivate` |
| POST | `/staff/{user_id}/reactivate` |
| PUT | `/staff/guards/{user_id}` |
| PUT | `/staff/guards/{user_id}/password` |
| GET | `/supervisor/dashboard` |
| GET | `/supervisor/profile` |
| POST | `/supervisor/profile/changes` |
| DELETE | `/supervisor/profile/changes/{change_id}` |
| POST | `/supervisor/profile/changes/{change_id}/resend-otp` |
| POST | `/supervisor/profile/changes/{change_id}/verify` |

## Registration, campus requests, sign-in and password reset (decisions D-020, D-021)

Public routes (no token). Every code is 6 digits, emailed; resend has a cooldown and an hourly limit per address.

| Step | Request body | Answer |
|---|---|---|
| `POST /auth/organization-registrations` | `{organization_name, campus_name, admin_full_name, username, email, phone_number, password}` (all required; `campus_name` 2–100 characters, the first campus's name, D-021) | `201 {registration_id, status, email (masked), delivery, otp_expires_in_seconds, resend_available_in_seconds, registration_expires_at}` |
| `…/{registration_id}/verify` | `{otp}` | `200 {status: "COMPLETED", organization_id, organization_name, official_email, official_phone_number, campus_name, main_admin: {user_id, full_name, username, email, phone_number, role, is_main_admin}, registered_at}` — `organization_id` is the permanent Organization ID, `main_admin.user_id` the Main Admin ID |
| `POST /auth/campus-requests` | `{organization_name, organization_id, campus_name, admin_full_name, username, email, phone_number, password}`; the organization must match the **exact** name **and** the Organization ID of one active organization | `201 {application_id, status, email (masked), delivery, otp_expires_in_seconds, resend_available_in_seconds, application_expires_at}`; `404 ORGANIZATION_NOT_MATCHED`; `409 CAMPUS_NAME_TAKEN` / `USERNAME_TAKEN` |
| `…/{application_id}/verify` | `{otp}` | `200 {status: "PENDING_APPROVAL", organization_name, campus_name, email, email_verified_at, decision_deadline}`; `409 EMAIL_ALREADY_REGISTERED` / `EMAIL_ALREADY_PENDING` |
| `GET /campus-requests` (Main Admin) | — | pending verified requests, each `{id, proposed_campus_name, applicant_name, applicant_email, applicant_username, applicant_phone, email_verified, email_verified_at, decision_deadline, status, submitted_at, reviewed_at}` |
| `POST /auth/organization-admin/login` | `{organization_name, organization_id, email, password}` | `200 {login_id, email, otp_expires_in_seconds}`; `401 INVALID_CREDENTIALS`; `403 ADMIN_APPROVAL_PENDING` for a verified, undecided campus applicant with the right password |
| `…/login/{login_id}/verify` | `{otp}` | `200 {access_token, token_type, expires_in}`; the web app then opens `/dashboard` |
| `POST /auth/password-reset` | `{organization_id, email}` or `{supervisor_id, email}` (Guards: `{guard_id, email, operational_mode}`) | `202 {reset_id, status: "OTP_REQUIRED", message, otp_expires_in_seconds, resend_available_in_seconds, expires_at}` — the same whether or not an account matches |
| `…/{reset_id}/verify` | `{otp}` | `200 {reset_token, expires_in}` (one-time) |
| `…/{reset_id}/complete` | `{reset_token, new_password, confirm_new_password}` (12–128 characters, a letter and a digit, not the username or email) | `200 {status: "PASSWORD_CHANGED", message}`; every earlier session is signed out |

## Remaining Admin and Supervisor features (decision D-024, migrations 0024–0029)

Added on 2026-11-18; the schema now has **259 operations**. Signed-in Admins and Supervisors of an active
organization, own campus only, unless a row says otherwise. Guards `403` (except `/guard/incidents`); another campus's
records `404`; **Super Admin tokens `403 SUPER_ADMIN_NOT_ALLOWED`** on every organization route.

| Request | Body / query | Answer and rules |
|---|---|---|
| `GET /auth/me` | — | adds `account_id` (internal ID, for `?handled_by=`) and `is_main_admin` |
| `GET /audit-logs` | `?category=` (repeatable) `&actor_role=&actor=&action=&outcome=SUCCESS\|FAILURE\|DENIED&q=&since=&until=&limit=&offset=` | `{items: [{id, occurred_at, category, action, outcome, actor_user_id, actor_display_id, actor_name, actor_role, target_type, target_id, target_label, details}], total, limit, offset}`; Supervisors see operational categories and their own records |
| `GET /audit-logs/categories` · `GET /audit-logs/export` | same filters | the categories the caller may read · CSV (≤ 50 000 rows, formula-safe), audited `AUDIT_LOGS_EXPORTED` |
| `GET /campus-requests` · `GET /campus-requests/history` | history: `?status=&q=&limit=&offset=` | **Main Admin only** (`MainAdminDep`, others `403`). Pending requests · `{items, total, limit, offset, counts}` (rejected rows show `details_erased_at`, no applicant data) |
| `POST /campus-requests/authorization` (`/{id}/resend-otp`, `/{id}/verify {otp}`) | `{current_password}` | step-up: code emailed to the Main Admin; `verify` returns `{authorization_id, form_expires_at}` |
| `POST /campus-requests/{id}/approve` | `{authorization_id}` | consumes the grant; creates the campus and its Admin; `409` if already reviewed |
| `POST /campus-requests/{id}/reject` | `{current_password}` | erases the applicant's details (decision I) |
| `GET /incidents` | `?status=` (repeatable) `&priority=&assigned=me\|none\|<uuid>&q=&since=&until=&limit=&offset=` | `{items: [<Incident>], total, limit, offset, counts}`; `<Incident>` = `{id, reference INC-000001, title, description, status, priority, occurred_at, camera_id, camera_name, building/floor/area ids, location, assigned_to, assigned_name, created_by, created_by_name, resolution_note, closed_at, created_at, updated_at, alert_count, report_count, evidence_count}` |
| `POST /incidents` · `PUT /incidents/{id}` | `{title, description, priority, occurred_at, camera_id, location}` (+ create: `assigned_to, alert_ids, report_ids, evidence_ids`) | `201 <Incident>` · `<Incident>` |
| `GET /incidents/{id}` | — | `{incident, alerts, reports, evidence, participants, footage_gaps, history, person_search, ai_assistance_available: false}` |
| `POST /incidents/{id}/status` · `PUT …/assignee` · `POST …/notes` | `{status, note}` · `{user_id\|null}` · `{note}` | transition rules of D-024 item 4 (`409 STATUS_UNCHANGED` / `INCIDENT_CLOSED`, `422 NOTE_REQUIRED`); assignee notified |
| `PUT\|DELETE /incidents/{id}/{alerts\|reports\|evidence}/{target_id}` | — | link / unlink; `409 EVIDENCE_ALREADY_LINKED` |
| `PUT\|DELETE /incidents/{id}/participants/{guard_user_id}` · `GET\|POST …/messages` | `{body}` | invite a Guard to the case chat (notified) · case chat |
| `GET /incidents/assignable-staff` · `GET /incidents/for-alert/{alert_id}` · `GET /guard/incidents` | — | Admins and Supervisors of the campus · cases an alert belongs to · a Guard's own case chats |
| `GET /alerts` | adds `?q=&since=&until=&handled_by=` | alerts add `handled_by, handled_by_name, handled_at, recipient_count, acknowledged_count, responding_count, has_snapshot` |
| `GET /alerts/{id}/escalations` | — | `[{id, scope: CAMPUS\|LOCATION\|GUARDS\|AUTOMATIC\|ASSISTANCE, escalated_by_name, escalated_by_role, note, added_count, created_at, guards}]` |
| `PUT /alerts/{id}/handler` | `{user_id\|null}` | open alerts only; `422 INVALID_HANDLER`; handler notified; audited |
| `GET /alerts/{id}/snapshot` · `GET /alerts/{id}/clip` | — | `image/jpeg` (detector snapshot, else the recorded frame) · `video/mp4` around the detection; viewing only, nothing saved; clip audited `ALERT_CLIP_VIEWED` |
| `GET /evidence` | adds `?tag=&building_id=&floor_id=&area_id=&incident_id=` | `<Evidence>` adds `tags, incident_id, incident_reference, building_id, floor_id, area_id, location`; `source` adds `PERSON_SEARCH` |
| `POST /evidence/from-alert\|from-playback` · `PATCH /evidence/{id}` | add `tags` (≤ 10) and, when saving, `incident_id` (an open case) | `422 INVALID_TAG` / `TOO_MANY_TAGS` |
| `GET /organization/settings` · `PUT /organization/settings` | PUT: `{official_phone, address_line, city, country, time_zone, date_format, time_format, auto_escalation_enabled, escalation_high_seconds, escalation_standard_seconds, assistance_broadcast_enabled, current_password}` | every Admin reads `{profile, preferences, alert_defaults, is_main_admin, can_edit, campus, security, campus_requests, updated_at, updated_by_name}`; **only the Main Admin** saves |
| `GET /organization/preferences` | — | `{time_zone, date_format, time_format}` for every signed-in user |
| `GET /ai-configuration` | — | `{modules, platform, campus, campus_permitted, campus_customized, campus_updated_at, campus_updated_by_name, alert_rules, cameras: [{camera_id, camera_name, location, configured, effective, customized, stream_state, running, running_now, frames_per_second, last_error, reported_at, updated_by_name, updated_at}]}` |
| `PUT /ai-configuration/campus` · `PUT /ai-configuration/cameras/{id}` | five module switches + `current_password` | campus: **Admins only**; camera: Admins and Supervisors; never beyond the level above (D-028 additions below) |
| `GET /live/cameras/{id}/overlay` | — | `{camera_id, captured_at, stale, frame_width, frame_height, modules, detections: [{label, confidence, box: [x1,y1,x2,y2] fractions, track_id, module}]}` |
| `GET /person-search/model` · `GET /person-search/observations` (`…/{id}/crop`) | `?camera_id=&limit=` | whether photo search is available (and why not) · recent observed people |
| `POST /person-search/photo` (multipart `file`, `since`, `until`, `camera_ids`, `note`) · `POST /person-search/observation` | `{observation_id, since?, until?, camera_ids?, note?}` | `201 <Search>` = `{id, kind, created_at, created_by_name, period_from, period_to, camera_ids, note, has_reference, searched_observations, complete, limitations, candidates: [{id, camera_id, camera_name, location, observed_at, similarity, band, review, reviewed_by_name, has_crop, incident_id, incident_reference, evidence_id}]}`; photo search `503 REID_MODEL_UNAVAILABLE` without the model |
| `GET /person-search` · `GET /person-search/{id}` (`…/reference`) | — | earlier searches · one search |
| `POST /person-search/results/{id}/review` · `…/incident` · `…/evidence` | `{review: PENDING\|VERIFIED_MATCH\|NOT_A_MATCH, note?}` · `{incident_id}` · `{title, notes?, tags?}` | only a LIKELY_MATCH can be verified; only a verified result is linked; `201 {evidence_id}` |
| `GET /person-search/results/{id}/crop` · `…/clip` | — | the result's picture · a short clip around it (viewing only, audited) |
| `GET /organization-admin/profile` · `POST …/changes` (`/{id}/verify`, `/{id}/resend-otp`, `DELETE /{id}`) · `POST …/regenerate-id` | changes: `{current_password, full_name?, username?, email?, phone_number?, new_password?, confirm_new_password?}`; verify `{otp, new_email_otp?}`; regenerate `{current_password}` | the Admin's own profile; email changes confirm both addresses; a new Admin ID |
| `GET /user-records` (`/export`, `/lookup?display_id=`, `/{id}`) | `?role=&status=&q=&include_deleted=&limit=&offset=` | **Admins only**: records `{items, total}`, CSV, lookup by ID, detail with status history and activity |
| `POST /user-records/{id}/rejoin` | `{full_name, username?, phone_number?, new_password?, location?, current_password}` | the same account becomes ACTIVE again |
| `POST /user-records/{id}/permanent-delete` | `{current_password, confirmation: "DELETE"}` | former staff only; `409 STILL_ACTIVE` / `ALREADY_DELETED` |
| `POST /staff/guards/{id}/regenerate-id` | `{current_password}` | Admins and Supervisors; `{display_id}`; `403 SUPERVISOR_ID_PERMANENT` for Supervisors' IDs |
| `GET /analytics` · `GET /analytics/export` · `GET /analytics/dashboard` | `?period=TODAY\|LAST_7_DAYS\|LAST_30_DAYS\|CUSTOM&since=&until=&building_id=&floor_id=&area_id=` (+ `format=csv\|pdf`) | the report (overview, alerts by type / status, daily, response times, Guard activity, manual reports, locations, camera uptime, storage, person observations) · CSV / PDF, audited · the dashboard feed (last 7 days) |

Notifications gain `incident_id` and the kinds `INCIDENT_ASSIGNED`, `INCIDENT_STATUS_CHANGED`, `INCIDENT_INVITED`,
`ALERT_AUTO_ESCALATED`, `ALERT_ASSISTANCE_BROADCAST`, `ALERT_HANDLER_ASSIGNED`, `GUARD_ID_REGENERATED`.

**Internal endpoints** (`X-Service-Token`): `GET /internal/cameras/{id}/ai-config` (modules to run now and detection
parameters), `POST /internal/cameras/{id}/ai-status {modules, frames_per_second?, error?, worker?}` (204),
`POST /internal/reid/observations {camera_id, track_id, observed_at, embedding[512], model_version, quality?,
crop_jpeg_base64?}` (`201 {observation_id}`; `422 REID_DISABLED` when the camera may not run Re-ID),
`POST /internal/reid/purge-expired`, `POST /internal/alerts/escalate-overdue` (`{escalated}`; the API also runs it itself).

## Live monitoring, recordings, retention and the evidence vault (decision D-023, migration 0023)

Signed-in Admins and Supervisors of an active organization, own campus only (Guards `403`; another campus's
camera, segment or evidence `404`; Super Admin tokens are refused and the platform role has no grant).

| Request | Body / query | Answer |
|---|---|---|
| `GET /live/cameras` | `?q=&building_id=&floor_id=&area_id=` | `[{camera: <CameraResponse>, status: {state: ONLINE\|OFFLINE\|UNKNOWN, last_seen_at, last_error, source: INGESTION\|HEALTH_CHECK\|null, state_changed_at, reported_at}}]` — `last_error` is generic, never the address |
| `POST /live/sessions` | `{camera_ids: [uuid]}` (`[]` = every camera) | the same list for those cameras; audited `LIVE_VIEW_STARTED` once per view; `404` for a camera outside the campus |
| `GET /live/cameras/{id}/frame` | — | `image/jpeg`, `Cache-Control: no-store`, `Last-Modified`; `404 LIVE_FRAME_UNAVAILABLE` / `LIVE_FRAME_STALE` (older than `LIVE_SNAPSHOT_STALE_SECONDS`) |
| `POST /live/cameras/{id}/health-check` | — | `{reachable, checked_at, latency_ms, detail, camera: <MonitoredCamera>}`; `422 NO_STREAM_URL`; audited `CAMERA_HEALTH_CHECKED`. Unreachable → OFFLINE at once |
| `GET /recordings` | `?camera_id=&q=&building_id=&floor_id=&area_id=&since=&until=&limit=500&offset=` (default the last 24 h; at most 31 days) | `{since, until, retention_seconds, cameras: [<MonitoredCamera>], segments: [{id, camera_id, camera_name, started_at, ended_at, duration_seconds, size_bytes, expires_at}] (newest first), markers: [{alert_id, camera_id, alert_type, priority, status, detected_at, last_detected_at}]}`; `422 INVALID_PERIOD` / `PERIOD_TOO_LONG` |
| `GET /recordings/{id}` | — | one segment (without `expires_at`) |
| `GET /recordings/{id}/media` | — | `video/mp4` (byte ranges supported), `no-store`; audited `RECORDING_PLAYED`; `404 RECORDING_FILE_MISSING` |
| `GET /recordings/retention` | — | `{retention, default_retention: {value, unit: MINUTES\|HOURS\|DAYS, seconds, is_default, updated_at, updated_by, updated_by_name}, storage: {node, total_bytes, used_bytes, free_bytes, percent_used, level: OK\|WARNING\|CRITICAL, reported_at} \| null, usage: {recording_segments, recording_bytes, oldest_recording_at, evidence_items, evidence_bytes}, provider: "LOCAL_DISK", warning_percent, critical_percent}` |
| `PUT /recordings/retention` | `{value, unit, current_password}` (1 minute – 365 days) | the overview; Admins **and** Supervisors, own password; `403 CURRENT_PASSWORD_INCORRECT`, `429 ACCOUNT_LOCKED` (with `Retry-After`), `422 INVALID_RETENTION`; audited `RECORDING_RETENTION_CHANGED` |
| `GET /evidence` | `?q=&kind=CLIP\|SNAPSHOT&camera_id=&alert_id=&since=&until=&limit=100&offset=` (`since`/`until`: the covered period overlaps) | `[<Evidence>]` newest first: `{id, kind, title, notes, investigation_ref, source: ALERT\|PLAYBACK, alert_id, camera_id, camera_name, covers_from, covers_to, media_type, size_bytes, sha256, saved_by, saved_by_name, saved_at, updated_at}` |
| `POST /evidence/from-alert` | `{alert_id, kind, title, notes?, investigation_ref?}` | `201 <Evidence>`; `422 NO_RECORDING` (nothing recorded then); `503 CLIP_TOOL_UNAVAILABLE`; audited `EVIDENCE_SAVED` |
| `POST /evidence/from-playback` | `{segment_id, kind, start_offset_seconds, end_offset_seconds (clips), title, notes?, investigation_ref?}` | `201 <Evidence>`; `422 INVALID_OFFSET` / `CLIP_TOO_LONG` (over `EVIDENCE_CLIP_MAX_SECONDS`) |
| `GET /evidence/{id}` / `…/media` | — | details / the file (`video/mp4` or `image/jpeg`, `no-store`); the media request is audited `EVIDENCE_VIEWED` |
| `PATCH /evidence/{id}` | `{title, notes, investigation_ref}` | `<Evidence>`; audited `EVIDENCE_UPDATED` with the changed field names |
| `POST /evidence/{id}/delete` | `{current_password}` | `204`; **Admins only** (`403 ADMIN_ONLY`), own password; audited `EVIDENCE_DELETED`; `500 EVIDENCE_FILE_CLEANUP_FAILED` if the record went but the file could not be erased |

Notifications gain `camera_id` and the kinds `CAMERA_OFFLINE`, `CAMERA_ONLINE` (campus Admins and Supervisors) and
`STORAGE_WARNING`, `STORAGE_CRITICAL` (Admins and Supervisors of every active campus; no target).

**Internal service endpoints** (header `X-Service-Token` = `INTERNAL_SERVICE_TOKEN`; `503 SERVICE_INTAKE_DISABLED`
when unset, `401` for a wrong token; no user token works):

| Request | Body | Answer |
|---|---|---|
| `GET /internal/cameras` | — | cameras of every active campus: `[{camera_id, organization_id, campus_id, display_name, connection_type, stream_url}]` |
| `POST /internal/cameras/{id}/status` | `{state, error?, seen_at?}` | `{outcome: CHANGED\|UNCHANGED, previous_state}`; `404 CAMERA_NOT_FOUND` |
| `POST /internal/recordings/segments` | `{camera_id, started_at, ended_at, relative_path, size_bytes}` (path inside the camera's own `recordings/` folder) | `201 {segment_id, outcome: REGISTERED\|DUPLICATE}`; `422 INVALID_PATH` / `PATH_OUTSIDE_CAMERA` / `INVALID_SPAN` |
| `GET /internal/recordings/expired` | `?limit=500` | `[{segment_id, organization_id, campus_id, camera_id, relative_path, started_at, retention_seconds}]`, oldest first |
| `POST /internal/recordings/purge` | `{segment_ids}` | `{purged}` |
| `POST /internal/storage/report` | `{total_bytes, used_bytes, free_bytes}` | `{level, notified}` |

## Guard mobile app, real-time signals, voice calls and push tokens (decision D-025, migration 0030)

Added on 2026-11-19; the schema now has **273 operations** plus the WebSocket. The complete contract — every body,
field, enum, error code, WebSocket message, setting and device-storage key — is the SRS section at the end of
`FYP_Documentation/FYP_Final_Guards_Mobile_App_Pages_and_Their_Each_Page_Features.txt`. Calls and the WebSocket serve
Guards, Supervisors and Admins of the campus who may see the alert or case; other campuses `404`; Super Admin tokens
`403 SUPER_ADMIN_NOT_ALLOWED` (WebSocket close `4403`).

| Request | Body / query | Answer and rules |
|---|---|---|
| `GET /alerts/{id}/responders` | — | `[{full_name, state: ACKNOWLEDGED\|RESPONDING, assessment, reason, acknowledged_at, responding_at, is_me}]`; staff and the alert's Guard recipients; no Guard IDs |
| `GET /alerts/{id}/calls/active` · `GET /incidents/{id}/calls/active` | — | `{voice_calls_available, provider: none\|livekit, call: <Call>\|null}` |
| `POST /alerts/{id}/calls` · `POST /incidents/{id}/calls` | — | `201 {outcome: STARTED, call, connection}` or `200 {outcome: JOINED, …}` when one is active; `503 VOICE_CALL_NOT_CONFIGURED`; `409 ALERT_CLOSED` / `INCIDENT_CLOSED` |
| `GET /calls` · `GET /calls/{id}` | — | active calls ringing for / joined by the caller · one call |
| `POST /calls/{id}/join` · `/decline` · `/leave` · `/end` | — | `{outcome, call, connection}` · `<Call>` (`409 NOT_INVITED`) · `{call, call_ended}` · `<Call>` (`403 CALL_END_NOT_ALLOWED` unless staff or the starter); `409 CALL_ENDED` |
| `POST /guard/device-tokens` | `{token, platform: ANDROID\|IOS, provider: FCM\|APNS, app_version?}` | `201 {id, platform, provider, push_delivery_mode}`; Guards only |
| `DELETE /guard/device-tokens/{token}` | — | `204`; `404 DEVICE_TOKEN_NOT_FOUND` |
| `GET /realtime/status` | — | `{websocket_path, heartbeat_seconds, auth_timeout_seconds, voice_call_provider, voice_calls_available, push_delivery_mode}` |
| `WS /realtime/ws` | first message `{type: "auth", access_token}`; then `{type: "ping"}` | `ready` / `pong` / `event` (`event`, `alert_id?`, `incident_id?`, `call_id?`, `state?`, `kind?`, `at`) / `session_ended` (`code`, `detail`) / `error`; close `4401`, `4403`, `4408`, `4429`, `1001` |

`<Call>` = `{id, scope: ALERT|INCIDENT, alert_id, incident_id, status: ACTIVE|ENDED, media: AUDIO, started_by,
started_by_name, started_by_role, started_at, ended_at, end_reason: ENDED_BY_USER|EVERYONE_LEFT|TARGET_CLOSED|null,
joined_count, my_state, can_end, participants: [{user_id, full_name, role, state: INVITED|JOINED|DECLINED|LEFT|MISSED,
invited_at, joined_at, left_at, is_me}]}`; `connection` = `{provider: "livekit", url, room, identity, token,
expires_in}`. `GET /guard/profile` adds `campus_name`, `status`, `operational_mode`. Audit actions
`ALERT_CALL_STARTED|JOINED|DECLINED|LEFT|ENDED` and `INCIDENT_CALL_…`.

## Web real-time messaging and voice calls, Web ↔ Mobile parity (decision D-026, migration 0031)

Added on 2026-11-19. The web app (Admins and Supervisors) uses exactly the routes and WebSocket above; nothing was
removed or renamed, so the Guard app is unchanged apart from reading the two new optional fields.

| Change | Contract |
|---|---|
| `<Call>` gains two fields (all call answers, `GET /calls`, `…/calls/active`) | `target_title: string` — `"<Alert type> alert · <camera>"` (e.g. `"Weapon alert · Gate Camera"`) or `"INC-000007 · <case title>"`; `target_location: string \| null` — `"Building → Floor → Area"` of the alert or case |
| `incident.updated` signal (migration 0031) | now sent for **every case history entry** (note, status, assignment, link, edit …), not only status changes; carries `kind` (the history kind, e.g. `NOTE_ADDED`, `STATUS_CHANGED`) and `state` (new status, status changes only). IDs only, as before |
| Web session end | the web client signs out on `401` and on `403 ORGANIZATION_INACTIVE` (REST) and on `session_ended` / close `4401` / `4403` (WebSocket); other `403` codes do not sign out |
| Dev proxy | `website/vite.config.ts` forwards `/api` **including WebSocket upgrades** (`ws: true`); a production reverse proxy must forward `Upgrade` for `/api/v1/realtime/ws` |

Web names (for reviewers): token `sessionStorage["cctv.accessToken"]`; socket client `website/src/realtime/socket.ts`
(`RealtimeSocket`, back-off 1 s … 30 s); page hook `useLiveRefresh(reload, match, {debounceMs, fallbackMs})`; call
routes `website/src/features/calls/api.ts` (`callsApi.active|start|mine|get|join|decline|leave|end`); voice adapter
`VoiceClient` (`LiveKitVoiceClient`, lazy `livekit-client` chunk); UI `CallControls`, `IncomingCallBanner`, `CallDock`.

## AI Investigation & Security Analytics Chatbot (decision D-027, migration 0032)

Added on 2026-11-19. Read-only: no route here changes an alert, case or any other record, and nothing can be uploaded.
Admins and Supervisors of the campus (Supervisors: operational scope — see D-027 item 9); Guards `403 STAFF_ONLY`;
Super Admin tokens `403 SUPER_ADMIN_NOT_ALLOWED`. Chats are private to their owner (Row-Level Security); another
user's or campus's chat is `404 ASSISTANT_SESSION_NOT_FOUND`. The full SRS (every field, enum, block and setting) is
at the end of `FYP_Documentation/FYP_Final_Organization_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt`.

| Request | Body | Answer and rules |
|---|---|---|
| `GET /assistant/status` | — | `{provider: deterministic\|ollama\|openai_compatible, provider_label, model_name, available, detail, read_only: true, file_uploads: false, retention_days, role, capabilities: [{key, title, description, examples}], tools, safety_note}` |
| `GET /assistant/sessions` | — | `[<Session>]`, the caller's own, most recent first |
| `POST /assistant/sessions` | `{title?: ≤120, context_incident_id?: uuid}` | `201 <Session>`; `404 INCIDENT_NOT_FOUND` |
| `GET /assistant/sessions/{session_id}` | — | `<Session> + {messages: [<Message>]}` |
| `DELETE /assistant/sessions/{session_id}` | — | `204` (messages deleted with it; audited `ASSISTANT_SESSION_DELETED`) |
| `POST /assistant/sessions/{session_id}/messages` | `{content: 1–1000 chars}` | `201 {session, user_message, assistant_message}`; `409 ASSISTANT_SESSION_FULL` after 400 messages; `422` blank |
| `POST /assistant/incidents/{incident_id}/investigate` | — | `201 {session, user_message, assistant_message}`: timeline, related events (±30 min) and evidence gaps in the caller's latest chat about the case (a new one if none); `404 INCIDENT_NOT_FOUND` |
| `POST /internal/assistant/purge-expired` | header `X-Service-Token` | `{deleted, retention_days}`: chats inactive > `AI_CHAT_RETENTION_DAYS` (storage retention daemon) |

`<Session>` = `{id, title, context_incident_id, context_incident_reference ("INC-000012"|null), created_at, updated_at,
last_active_at, message_count}`. `<Message>` = `{id, session_id, role: USER|ASSISTANT, content, citations:
[{entity_type: ALERT|INCIDENT|CAMERA|EVIDENCE|MANUAL_REPORT|LOCATION, id, label, route}], tool_calls: [{tool, arguments,
outcome: OK|EMPTY|DENIED|ERROR, rows, latency_ms, error?}], blocks: [metrics|table|bars|timeline|notice],
insufficient_evidence, provider, model_name, created_at}`. Audit category `AI_ASSISTANT` (actions `ASSISTANT_QUERY`,
`ASSISTANT_TOOL_CALL`, `ASSISTANT_SESSION_DELETED`); `GET /audit-logs?category=AI_ASSISTANT` works for Admins (all)
and Supervisors (own records only). Settings: `AI_LLM_PROVIDER`, `AI_LLM_BASE_URL`, `AI_LLM_MODEL` (`qwen2.5:7b`),
`AI_LLM_API_KEY`, `AI_LLM_TIMEOUT_SECONDS` (30), `AI_LLM_ALLOW_EXTERNAL` (false), `AI_CHAT_RETENTION_DAYS` (30).

## AI feature controls (decision D-028, migration 0033)

effective(m) = platform AND NOT organization-blocked AND campus AND camera; a camera without its own switches runs
nothing. Module keys: `human_detection`, `person_reid`, `weapon_detection`, `fight_detection`, `face_recognition`.

| Route | Body | Response / rules |
|---|---|---|
| `GET /ai-configuration` | — | as D-024 plus `available_modules` (what the caller may switch on), `org_blocked` (five booleans), `blocked_by_platform` (keys), `supervisor_visible_modules` (Admins: five booleans; Supervisors: `null`). For Supervisors, hidden modules are left out of `modules` and `alert_rules` and shown as off in every switch set |
| `PUT /ai-configuration/campus` | five switches + optional `supervisor_visible_modules` + `current_password` | **Admins only**; a blocked module switched on ⇒ `403 AI_MODULE_BLOCKED_BY_PLATFORM`; beyond the platform ⇒ `409 MODULE_NOT_PERMITTED`; audit `AI_SETTINGS_UPDATED` with `supervisor_visible_changed` |
| `PUT /ai-configuration/cameras/{id}` | five switches + `current_password` | Admins and Supervisors; hidden module switched on by a Supervisor ⇒ `403 AI_MODULE_HIDDEN_FROM_SUPERVISOR` (switched off: ignored, kept as it was); blocked ⇒ `403 AI_MODULE_BLOCKED_BY_PLATFORM` |
| `PUT /ai-configuration/cameras/bulk` | `{camera_ids: uuid[1..200], mode: "ADD"\|"REMOVE"\|"SET", modules: {key: bool}, current_password}` | `{mode, updated_count, cameras: [CameraAiResponse]}`; own campus, all or nothing (another campus's camera ⇒ `404 CAMERA_NOT_FOUND`); ADD/REMOVE need ≥ 1 module (`409 NO_AI_MODULE_SELECTED`); Supervisors' hidden modules kept; audit `CAMERA_AI_BULK_UPDATED` |
| `POST /cameras` · `PUT /cameras/{id}` | existing body + optional `ai_modules: {key: bool}` | add without `ai_modules` ⇒ all off; edit without it ⇒ unchanged; keys left out keep their value; same 403 / 409 rules; audit `CAMERA_AI_SETTINGS_UPDATED` (`with_camera_details: true`) |
| `GET /cameras` · `GET /cameras/{id}` · camera responses | — | `ai_modules` (selected, visible to the caller) and `ai_effective_modules`; `null` for roles without AI access |
| `GET /internal/cameras/{id}/ai-config` | service token | unchanged shape; `modules` now follows the four levels (empty for a camera nobody opted in) |
| `GET /platform/organizations/{organization_id}` | platform token | adds `ai_permissions: [{module_key, feature, label, core, blocked, platform_enabled, status: ALLOWED\|BLOCKED\|DISABLED_GLOBALLY}]` |
| `GET /platform/organizations/{organization_id}/ai-permissions` | platform token | `{organization_id, ai_permissions}` |
| `PUT /platform/organizations/{organization_id}/ai-permissions` | `{module_key, blocked, current_password}` (key or long feature name) | one feature per call; SA-SEC-4 password (wrong ⇒ `403`, counted, audited); unknown module ⇒ `409 UNKNOWN_AI_MODULE`; unknown organization ⇒ `404`; idempotent; audit `ORGANIZATION_AI_PERMISSIONS_UPDATED` (platform log); blocking switches the module off on every camera of the organization, unblocking switches nothing on |
