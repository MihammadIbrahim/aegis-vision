# Decision Log

## D-001 — Tenant and campus isolation (2026-10-26)

**Decision:** One shared PostgreSQL schema. Every operational table carries `organization_id` and `campus_id`, and PostgreSQL Row-Level Security limits each row to users of the same campus.

**How it works**
- Services connect as a restricted role (`APP_DB_USER`). It is not a superuser, does not own the tables, and cannot bypass RLS. Migrations run as the owner.
- Per request, the API sets only `app.user_id` (transaction-local). Database functions (`app_actor_org_id()`, `app_actor_campus_id()`, `app_actor_role()`, `app_actor_is_first_admin()`) look up the rest from `users`, and only for `ACTIVE` accounts.
- Application queries also filter by the caller's organization and campus, so the rule is enforced twice.
- Composite foreign keys `(organization_id, campus_id) → campuses` stop a row from pointing at another organization's campus.

**Allowed cross-campus exceptions (requirements, Section 2)**
1. `app_admin_directory(org)`: active Admin names and the First Admin flag, for the caller's own organization only.
2. The First Admin can read and decide their organization's campus registration requests, and can create the approved campus and its Admin. They still cannot read any other campus's data.

**Every new campus-scoped table must:** have both columns, use the composite foreign key, enable RLS with the `cameras_same_campus` policy pattern from migration `0001`, and grant the app role only the rights it needs.

**Open items**
- Platform Super Admin access: not modelled yet; it must not use this app role.
- Applicant submission flow (public, before login) needs its own insert path for `campus_registration_requests`.
- Approval should also require the First Admin's password (Org Admin page 15, feature 9) and write an audit-log entry.

## D-002 — Pre-login database access for registration (2026-10-27)

**Decision:** Registration runs before anyone is signed in, so RLS (which needs a known user) cannot
apply. The app role gets **no** rights on the new tables (`organization_registrations`,
`email_otp_challenges`, `user_credentials`; RLS enabled with no policies). It may only call four narrow
`SECURITY DEFINER` functions from migration `0004`: submit, issue code, verify code, complete. Each does
one step, locks the registration row, and pins `search_path`. RLS on every other table is unchanged.
This closes D-001's open item for a public applicant path (first-Admin registration only; the
"Already Registered Organization?" path follows the same pattern in Stage 4).

## D-003 — One-time codes stored in PostgreSQL, not Redis (2026-10-27)

**Decision:** Email OTPs are stored in PostgreSQL as HMAC-SHA-256 hashes keyed with a server secret
(`OTP_SECRET_KEY`, or a key derived from `JWT_SECRET_KEY`), never as plain codes. This differs from the
architecture plan, which listed Redis for OTPs.

**Why:** Redis is not available in the current development environment. Keeping codes in the same
database lets "verify the code" and "create the organization" commit or roll back together, and
makes the per-address hourly limit exact under concurrency (advisory lock per address).

**Consequence:** The per-client-IP request limit is kept in API process memory for now (per replica,
reset on restart). It can move to Redis later behind `IRateLimiter`. The consolidated architecture
document still says Redis for OTPs and should be updated once this decision is reviewed.

## D-004 — Organization user passwords in a separate table (2026-10-27)

**Decision:** Organization users' password hashes live in `user_credentials`, not in `users`.

**Why:** The app role has table-wide `SELECT` on `users`, and RLS lets users of the same campus read
each other's rows. A hash column there would be readable by any same-campus query. `user_credentials`
has no grants to the app role at all; login (Stage 3) will read it through a narrow function.

## D-005 — Duplicate email reported only after OTP verification (2026-10-27)

**Decision:** At registration, a duplicate organization name or username is refused immediately. A
duplicate email is reported only after the emailed code is verified (`409 EMAIL_ALREADY_REGISTERED`).

**Why:** Refusing at submission would let anyone test which email addresses have accounts. After
verification, only the inbox owner learns it. Unique indexes still guarantee no duplicate is ever created.

## D-006 — Organization Admin sign-in reuses the organization access token (2026-10-27)

**Decision:** After credentials and the email OTP, the API issues the existing `tenant`-audience access
token (account ID only; role, organization and campus are read from the database on every request). There
is no separate Admin token type, and no refresh token or logout. Neither exists anywhere in the project yet;
adding them is a later, project-wide decision.

**Also:** `organizations.status` (ACTIVE / INACTIVE / SUSPENDED, default ACTIVE) was added so sign-in and
the Admin routes can refuse inactive organizations. Nothing changes it yet; the Super Admin's organization
controls (requirements Section 22, still open) will.

## D-007 — Main Admin ownership transfer and campus-scoped Admin deletion (2026-10-27)

**Decision:** "Main Admin" is the existing `users.is_first_admin` flag; a transfer moves it in one
transaction, and the existing unique index keeps one Main Admin per organization. Deleting an Admin
account deletes that Admin's campus and its records (users, cameras, pending creations), never another
campus, and never the organization row.

**Choices made where the task did not say (please review):**
- **Shared campus.** If another active Admin works on the same campus, only the deleting Admin's own
  account is deleted and the campus stays.
- **Last campus.** ~~When the last campus is deleted, the organization becomes `INACTIVE`…~~
  Superseded by D-008: the organization is now deleted.
- **No successor.** A Main Admin whose organization has other campuses but no eligible Admin (active, on
  an active campus, with a password set) cannot delete (`409 OWNERSHIP_SUCCESSOR_REQUIRED`).
- **Acceptance** is an explicit accept by the signed-in recipient, with no extra password or code.
  Requests expire after `OWNERSHIP_TRANSFER_ACCEPT_HOURS` (default 72).
- **Audit** uses `platform_audit_logs` (requested, accepted, rejected, deleted). No passwords, codes or
  emails are written there.
- **Training dataset.** No training-data module or table exists yet (`ml_training` is placeholders).
  Deletion touches only listed database tables and no files, so a future dataset is not affected.

## D-008 — Complete campus erasure, optional export, deleted organization names (2026-10-28)

**Decision (from the finalized requirements):** deleting a campus's last active Admin erases the campus
and everything of it. Deleting the organization's last campus erases the organization too; only its
name is kept, in `deleted_organization_names`, for the Super Admin. This replaces D-007's "last campus →
INACTIVE".

**Choices made where the requirements did not say (please review):**
- **Name history holds the name only.** No date, ID, campus or contact. Two deleted organizations with
  the same name appear twice. Each deletion writes exactly one entry, inside the same transaction.
- **Completed ownership transfers are kept** (requirement: "preserve … the completed ownership
  transfer"). The deleted party's link becomes empty. Unfinished requests of deleted users are erased.
  *Refined by D-009: kept only while the recipient exists.*
- **Organization contacts.** The official email and phone came from the first Admin at registration.
  They are cleared when they belong to a deleted user, even if the organization stays.
- **Audit.** When the organization is deleted, the platform audit entries naming its Organization ID
  are erased. ~~One `ADMIN_ACCOUNT_DELETED` entry without identifiers is written.~~ Since D-009 none is
  written. Entries never named
  campuses, so a campus-only deletion has nothing campus-specific to remove.
- **Campus files** live under `CCTV_STORAGE_ROOT/<organization>/<campus>/` (the folder planned in
  `.env.example`). Nothing writes there yet. Deletion moves the folder out of reach before the commit,
  puts it back if the commit fails, and erases it afterwards.
- **Export** is a JSON download built in memory from the database, with no file written. It covers
  campus, users, cameras and pending account creations. Categories the system does not hold yet are
  listed as not included.

## D-009 — Ownership history keeps the previous owner's identity (2026-10-28)

**Decision (from the requirements):** when a transfer is accepted, the previous owner's full name,
username and email are copied onto the transfer record. The current Main Admin sees the accepted
transfers made to them, including after the previous owner's account and campus are deleted. This is
the only personal data kept for a deleted Admin. The organization name in the Super Admin list is the
only data kept for a deleted organization.

**Details:**
- **When the snapshot is taken:** at acceptance, by a database trigger. It cannot change afterwards,
  and pending, rejected, cancelled and expired requests carry none.
- **Who sees it:** only while the viewer is the Main Admin, and only transfers to that viewer. A former
  Main Admin, ordinary Admins, Supervisors, Guards and the Super Admin get nothing. This is enforced by
  the API and by the database function.
- **When the record goes:** with its recipient (nobody could see it any more), or with the
  organization.
- **No audit entry** is written when an organization is deleted.

## D-010 — Location permissions (approved by the project owner, 2026-10-28)

**Source:** the project owner's task of 2026-10-28. It answers, for locations, the open item
"Branch permissions" in the requirements' list of points not yet decided. It **changes** the
Supervisor dashboard page *12. Building & Location Management*, which listed viewing only.

| | Admin (Main or additional) | Supervisor | Guard |
|---|---|---|---|
| Buildings / outdoor perimeters | view, create, edit, remove | view only | none |
| Floors | view, create, edit, remove | view, create, edit, remove | none |
| Areas / zones (incl. boundaries) | view, create, edit, remove | view, create, edit, remove | none |
| Guard locations | view, set, remove | view, set, remove | own, read-only |
| Camera locations | view, set, remove | view, set, remove | none |

- **Scope:** everything is limited to the user's own campus. The Main Admin gets no access to other
  campuses' locations, summaries or selectors.
- **Unchanged:** Main Admin, ownership-transfer and account rules.

## D-011 — Location model and rules (new implementation decisions, 2026-10-28)

These are **new decisions made during implementation**, where the documentation was silent. They are
not earlier approved requirements; please review.

1. **Boundaries live on areas.** Section 3 does not say which level they belong to. Areas are where
   Guards and cameras are allocated, and both roles that the documents let configure boundaries
   (the Admin and Supervisor camera pages) can edit areas.
   - **Values:** NORTH / SOUTH / EAST / WEST / CUSTOM, plus a custom name, which is required only
     for CUSTOM. An optional INSIDE / OUTSIDE label.
2. **An area's floor is optional**, in indoor buildings too (for example a one-storey building).
   Outdoor perimeters have no floors at all; the database enforces both rules.
3. **One link per Guard or camera.** Each points at exactly one building, floor or area: the most
   specific level chosen in the dropdowns. The rest of the path is derived, so it cannot become
   inconsistent. One location per Guard (the documents use the singular "assigned location").
4. **Names** are unique within their parent, ignoring capitalization, and stored as typed:
   - buildings per campus;
   - floors per building;
   - areas per building and floor.

   A floor's optional level number is unique per building.
5. **Renaming** is always allowed; IDs never change. **Changing a building to a perimeter** is refused
   while it has floors. Areas can move between floors of their own building, never to another
   building.
6. **Removal only when empty.** A building, floor or area with sub-locations, cameras or Guards is
   refused (`409 LOCATION_NOT_EMPTY`). The foreign keys never cascade. The only exception is campus
   deletion (D-008), which removes the whole campus hierarchy explicitly.
7. **No audit entries** for location changes yet. The documents require them, but no
   organization-level audit log exists; only the platform's, which is for Super Admin events.
8. **Guards** cannot read the location tables. They see only their own assignment, through a narrow
   database function.

## D-012 — Camera Management (2026-10-28)

**From the project owner (approved):** Admins and Supervisors both have full camera management in
their own campus (list, view, add, edit, remove, set location). Guards cannot manage cameras.
Buildings and outdoor perimeters stay Admin-only (D-010 unchanged).

**New implementation decisions** (the documents were silent; please review):
1. **Camera ID** is the system-generated `id` (Admin dashboard item 4: "system-maintained"). No
   second, readable code was invented.
2. **Fields** come from the documents: display name; connection type RTSP or ONVIF (item 6); one
   optional "IP Address / Stream URL" (item 7).
   - **Why the URL is optional:** a camera can be registered before equipment exists (Section 13:
     development uses recorded video first).
   - **What is checked:** RTSP needs `rtsp://` or `rtsps://`, ONVIF needs `http://` or `https://`.
     The host or IP and the port are also checked.
3. **No credentials.** A stream URL with a user name or password is refused (`422
   STREAM_CREDENTIALS_NOT_ALLOWED`), and the database refuses it too. No secure storage for camera
   credentials exists yet; adding one is a later decision.
4. **Unique per campus**, ignoring capitalization: the display name, and the stream URL when given.
   Other campuses may reuse both (a different site and network).
5. **Indoor/outdoor** is not stored. It comes from the linked building's kind, so it cannot
   disagree with the location.
6. **Location** reuses D-011: one link to a building, floor or area, checked by the same rules.
   `PUT /cameras/{id}` replaces details and location together. The Location Management
   `camera-links` routes stay available.
7. **Online/offline status** is not stored or invented. It needs the video-ingestion service,
   which does not exist yet.
8. **Removal** deletes the camera row. Nothing refers to cameras yet. When recordings, alerts or
   evidence exist, their links must stop a camera that has history from being removed.
9. **Database:** reading cameras is unchanged (same campus). Writing is now limited to the
   campus's Admins and Supervisors; before, any active campus user could write at the database
   level, although no route allowed it.
10. **No audit entries** yet (no organization audit log exists). This is out of scope by
    instruction.

## D-013 — Alert Management, manual reports, notifications, organization audit (2026-10-31)

**From the project owner (approved, 2026-10-31):**
- **Creation:** alerts are created from AI detections; manual reports are separate.
- **Priority:** weapon and fight/violence are high priority.
- **Routing:** location-based routing to Guards, within campus isolation.
- **Guards** acknowledge and report that they are responding.
- **Lifecycle:** NEW → ACKNOWLEDGED → RESPONDING → RESOLVED.
- **Supervisors** escalate or dismiss.
- **Also required:** notifications, alert chat, manual reports with an optional photo, offline sync,
  and audit logging.

**New implementation decisions** (the documents were silent or conflicting; please review):
1. **Statuses:** also ESCALATED (requirements' "Supervisor Escalated") and DISMISSED.
   - RESOLVED and DISMISSED are final; there is no reopening.
   - The database refuses any other change: NEW may go to any status; ACKNOWLEDGED → RESPONDING,
     ESCALATED or closed; RESPONDING → ESCALATED or closed; ESCALATED → closed.
2. **Who closes:** Admins and Supervisors resolve (optional note) and dismiss (reason required).
   Requirements 15.4 lets the Admin escalate too, so Admins can also escalate. Guards never close an
   alert: "Under Control" is an assessment only (15.3).
3. **Guard actions:**
   - **Order:** a recipient acknowledges, then may report RESPONDING and an assessment
     (UNDER_CONTROL / ADDITIONAL_GUARDS_REQUIRED). An assessment needs acknowledgement first;
     responding also counts as acknowledging.
   - **Alert status:** the first acknowledgement moves NEW → ACKNOWLEDGED, and the first responder
     moves it to RESPONDING. An escalated alert stays ESCALATED until staff close it.
4. **Routing:** a Guard receives an alert when their assigned building, floor or area **contains** the
   camera's location. For example, a Guard assigned to a building gets alerts from all its floors and
   areas; a Guard assigned to an area only from that area.
   - **No location:** a camera with no location reaches the campus's staff only.
   - **Unassigned Guards** never receive alerts automatically (15.2).
5. **Staff see every alert of their campus** and are notified of every new alert. Supervisor page 5
   lists only high-priority notifications; this decision notifies for all, and is easy to narrow.
6. **Campus-only escalation.** "Across the organization" (15.4) is implemented as the alert's campus,
   per campus isolation.
   - **Scopes:** CAMPUS (every active Guard), LOCATION (Guards assigned at or below a building, floor
     or area) or GUARDS (Guard IDs).
   - **Records:** each escalation is a separate record linked to the alert. Adding nobody is refused
     (`NO_ELIGIBLE_GUARDS`).
7. **"Additional Guards Required"** records the assessment and notifies the campus staff. It does
   **not** broadcast to other Guards automatically: 15.4's "eligible Guards" is undefined, so staff
   escalate. **Open for your decision.**
8. **Alert types:**
   - WEAPON and FIGHT_VIOLENCE are HIGH (enforced by the database);
   - RESTRICTED_AREA is STANDARD (Guard app screen 5);
   - human detection or tracking does not create alerts.

   Confidence thresholds stay open (Section 22).
9. **Detection intake.** AI workers call `POST /internal/detections` with a shared secret
   (`DETECTION_INGEST_TOKEN`; off when empty).
   - **Deduplication:** the same `detection_id` is ignored. A repeat of an open alert from the same
     camera and type within `ALERT_DEDUP_SECONDS` (default 60) is merged (count and last time).
   - **No AI worker exists yet;** nothing creates fake detections.
10. **The alert keeps the camera's location at detection time** (IDs and names), so history survives
    moves and renames. A **camera with alert history cannot be deleted** (`409 CAMERA_HAS_ALERTS`),
    as D-012 required once alerts existed.
11. **Alert chat** belongs to the alert, since no incident module exists.
    - **Who:** the campus's staff and the alert's Guards.
    - **When:** messages can be posted until the alert is closed, and read afterwards.
    - **Notifications:** none for messages.
    - **Audit:** records the message ID, never the text.
12. **Notifications** are in-app records listed by the API. **No push (FCM) or WebSocket**: neither
    integration exists.
13. **Manual reports:**
    - **Fields:** type (free text, 2–60 characters), description, building/perimeter → optional
      floor → optional area, optional time, optional JPEG/PNG photo (base64, at most
      `MANUAL_REPORT_PHOTO_MAX_BYTES`).
    - **Storage:** photos are kept under the campus folder (`CCTV_STORAGE_ROOT/<org>/<campus>/manual_reports/`).
    - **Separate from alerts:** a report never creates an alert.
    - **Notifications:** campus staff are notified.
    - **Review:** staff mark a report REVIEWED (this is the whole lifecycle).
    - **Location names for Guards:** they read the names only through a narrow function. This
      refines D-011 item 8, which gave Guards no location access; the report form's dropdowns
      (Guard screen 8) need it.
14. **Offline sync:**
    - **Duplicate protection:** every Guard action, report and message may carry a client ID, and a
      resend changes nothing.
    - **Batches:** `POST /guard/sync` applies up to 50 queued items in order, each on its own, and
      reports the result of each.
15. **Organization audit log** (`organization_audit_logs`):
    - **Recorded:** alert creation, each Guard action, resolve, dismiss, escalation, chat messages
      (metadata), report submission and review.
    - **Fields:** actor, role, action, target and small metadata. No secrets and no message text.
    - **Insert-only** for the API; there is no viewing endpoint yet (the Audit Logs module).
    - **Erased** with the campus (D-008).
16. **Campus deletion** erases the campus's alerts, recipients, escalations, events, messages,
    reports, notifications and audit rows. The **export** now includes alerts and manual reports
    (no photos, no chat).

## D-014 — CV processing foundation (2026-10-31)

**Decision (from the project owner's task):** build the cv_inference pipeline end to end before any
real model:
- frame input;
- detector, tracker and sink interfaces;
- event rules;
- delivery to the existing detection intake;
- a clearly marked synthetic detector for tests.

**Implementation decisions** (please review):
1. **Responsibilities:**
   - **The pipeline** confirms and throttles: label → alert-type map, per-label thresholds, N
     consecutive sampled frames, a cooldown per camera and type.
   - **Core API** owns alert identity (duplicate IDs, merging), priority and routing.
   - **Coordination:** the cooldown must be shorter than `ALERT_DEDUP_SECONDS`, enforced in the
     pipeline settings.
2. **Stable detection IDs:** a hash of the video identity, camera, alert type, episode start and
   event number, prefixed with the model name. Retries and re-runs are idempotent.
3. **Persons** are tracked but never sent, consistent with D-013 item 8.
4. **Failure policy:**
   - wrong token, intake disabled or unknown camera → stop;
   - 422 → event recorded as rejected;
   - network errors, 429 and 5xx → bounded exponential retries, then recorded as undelivered.

   Nothing is dropped silently, but undelivered events are not yet stored durably.
5. **The synthetic detector is selected explicitly** (`--detector synthetic`), reported as
   synthetic, and its IDs start with `synthetic-`.
6. **ONNX Runtime is the serving backend.** Asking for CUDA without it is an error, not a silent CPU
   fallback.
7. **The IoU tracker** is the first tracker; ByteTrack is a drop-in later.

**Backend fix (migration 0016):** detection IDs merged into an open alert were not remembered, so
re-sending them merged again and inflated `detection_count`. Found by the end-to-end test. Every
accepted detection ID is now recorded (`alert_detection_ids`).

## D-015 — First real model: YOLOX-Tiny person detector, ByteTrack-style tracking (2026-10-31)

**Approved by the project owner (2026-10-31):** downloading the official YOLOX-Tiny ONNX weights for
development use.

**Decisions** (please review the licence point):
1. **Model:** YOLOX-Tiny, official ONNX (Megvii-BaseDetection/YOLOX 0.1.1rc0). It runs on this
   project's CPU (i5-5200U, no usable GPU).
   - **Licences:** the code is Apache-2.0. The weights have **no separately stated licence**; they're
     COCO-trained (annotations CC BY 4.0, images under various Flickr licences). Accepted for
     development and the FYP. **Before commercial redistribution, check or replace them.**
   - **Not chosen:** Ultralytics YOLO (AGPL-3.0), YOLOX-Nano (too weak), RT-DETR (heavier on this CPU).
2. **Decoder specific to YOLOX:** no generic decoder; it follows the publisher's code. A metadata file
   (format, shapes, class index, SHA256, licences) must match the real model, or loading fails. There
   is no fallback to the synthetic detector.
3. **Persons only, and persons never alert** (D-013 item 8).
4. **Tracker:** `supervision` 0.31 no longer contains ByteTrack, so ByteTrack's published
   association (Kalman motion, high/low-score stages, lost buffer, tentative → confirmed) is
   implemented in `single_tracker.py`.
   - **Dependencies:** SciPy for the assignment step (already installed). `supervision` is removed
     from the CV requirements, since nothing uses it.
   - **Which tracker:** `CV_TRACKER=auto` keeps the IoU tracker with the synthetic detector, so
     existing behaviour and tests are unchanged.
5. **Track IDs stay camera-local:** tracks carry `camera_id`, and a `TrackObserver` hook exists for
   the future Re-ID stage. **No cross-camera matching is implemented.**

## D-016 — Organization Personnel Management, Excel/CSV import, restricted-area access (2026-11-01)

**Confirmed by the project owner (task of 2026-11-01):**
1. **Campus isolation:** personnel belong to **one campus**. Campus A and campus B never see each
   other's records, imports or permissions. The scope always comes from the session.
2. **Only the Organization Admin creates personnel,** both manually and by Excel/CSV import. The
   Admin also edits, deactivates and configures them, and grants restricted-area access. Enforced
   in the API, the use cases and the database.
3. **Supervisors** get the read-only directory, search and basic details. They see a photo only
   within an authorized identity or incident review. They cannot create, edit, deactivate,
   import, configure, or grant/revoke access. **Guards** get no directory access. **The Super
   Admin** has none either.
4. **Flexible Excel, controlled database:** spreadsheet headers are mapped onto a stable schema plus
   controlled custom fields. No database column is created from user input.
5. **All-or-nothing import:**
   - any blocking error rejects the whole file;
   - the Admin fixes the file and uploads it again;
   - the import happens only after explicit confirmation, validated again inside one transaction.

   Warnings are shown and must be acknowledged.
6. **No arbitrary row limit.** Server-protection limits are documented settings, and a file over a
   limit is refused as a whole.
7. **Restricted-area access** is a relationship (person ↔ restricted area) with validity,
   revocation and history, not a flag on the person.
8. **No invented identity:** an unknown or uncertain person is never reported as a named person
   entering without permission.
9. **Face recognition is out of scope** and will be a separate phase with its own decisions.

**Implementation decisions** (please review):
1. **Personnel ID:**
   - `PSN-` + 10 random characters (no I/O/0/1), unique across the platform;
   - email and phone are never identifiers;
   - the institutional ID (`external_id`) and the email are unique **per campus**, ignoring
     capitalization.
2. **Categories:**
   - eight built-in categories for every campus (an `AFTER INSERT` trigger on `campuses`, plus
     existing campuses in the migration);
   - each category's `required_fields` decides which optional fields it requires. Defaults: the
     institutional ID for student, faculty, employee, staff and management; the end date for
     contractors and visitors;
   - categories are deactivated, never deleted.
3. **Custom fields:**
   - typed definitions (TEXT, NUMBER, DATE, BOOLEAN, CHOICE, EMAIL, PHONE), each optionally limited
     to some categories;
   - values stored in `personnel.custom_attributes` (a JSON object) and checked in the
     application;
   - the key and type cannot change; built-in names are reserved.
4. **One validation module** (`personnel_rules.py`) for the form and the import. Messages never
   repeat submitted values, so issues can be stored and logged.
5. **Database role separation:**
   - **Admin-only Row-Level Security** on personnel, import, custom-field and grant tables;
   - **Supervisor reads** go through `app_personnel_directory`, `app_area_access_list` and
     `app_personnel_audit_trail`, which return basic columns. Email and phone are visible to
     Admins only;
   - **no DELETE right on `personnel`:** records are deactivated, never deleted.
6. **Deactivation:**
   - keeps the record (alerts, evidence and audit stay valid);
   - revokes the person's active area access;
   - reactivation does not restore that access.
7. **Import engine:**
   - streaming read-only openpyxl or `csv`, in chunks in a worker thread; pandas is not needed;
   - formulas and Excel errors are refused, never evaluated;
   - macro workbooks, legacy `.xls` and oversized unpacked archives are refused;
   - CSV must be UTF-8.
8. **The annotated workbook** is a separate write-only copy:
   - red for errors, amber for warnings, with comments, a result column and a report sheet;
   - values only, without the original's formatting; text starting with `=` stays text;
   - the upload is never changed, and its SHA-256 is checked before confirming.
9. **Confirmation:**
   - a per-campus advisory lock, a re-check of the file hash, and validation again while inserting,
     in **one transaction**;
   - any blocking problem rolls everything back, then the batch is validated again (INVALID) so
     the Admin sees what changed;
   - it runs within the request; proxy and client timeouts must allow large files.
10. **Rollback of an import** deactivates the records the batch created and revokes their access.
    Nothing is deleted.
11. **Files:** uploads and annotated workbooks are kept under the campus folder for
    `PERSONNEL_IMPORT_FILE_RETENTION_HOURS` (72), then erased. Unconfirmed batches then become
    DISCARDED; their history row stays.
12. **Restricted areas:**
    - a separate Admin-only marking table (`restricted_areas`), not a column on `areas`, which
      Supervisors may edit (D-010);
    - a mark can be cleared only without active grants;
    - an area with access history or a restricted mark cannot be deleted (409), like cameras with
      alert history (D-012/D-013).
13. **Grants:**
    - one active grant per person and area; several areas at once (all or none);
    - `valid_until` is exclusive;
    - an optional weekly schedule: ISO days, a same-day window and an IANA time zone. Campuses have
      no time zone, so each schedule names its own.
14. **The access policy** (`access_policy.py`) returns a decision, and `UNKNOWN_IDENTITY` when
    nobody was identified. Only a **verified** identity can support an unauthorized-entry claim.
    No identity-candidate table exists yet; its proposed shape is in `personnel_management.md`.
15. **Profile photo:** JPEG/PNG only, Admin-only. It is not a biometric template.
16. **Supervisors read current authorizations** (who may enter an area, a person's areas, the access
    check). The requirements' Supervisor *Authorized Persons — Entry Verification* page lists them,
    and they include no notes or revoked history. **Please confirm.**
17. **The empty `authorized_person_*` placeholder files** (seven, all 0 bytes) were removed. The
    personnel modules replace them.
18. **Web app:** `website/` (React + Vite + TypeScript, the approved stack), the first frontend. It
    covers sign-in and the personnel, import, restricted-access and configuration pages for
    Admins, with read-only pages for Supervisors.

## D-017 — Staff Management: Admin-only creation and removal, Guard categories (2026-11-11)

**Decided by the project owner (Phase 8 task and answers of 2026-11-11):**
1. **Only Organization Admins create Guards and Supervisors.** Until now Supervisors could create
   Guards (migration 0009, requirements Section 9.3). This is a requirement change: enforced in the
   API (`ADMIN_ONLY`) and in the database (migration 0018 re-creates the eight Guard step-up and
   creation functions with an Admin-only actor rule).
2. **Only Admins deactivate or reactivate** Guards and Supervisors. Deactivation sets the existing
   status `LEFT` (requirements: "Active / Left"): no sign-in, earlier sessions refused at once,
   nothing deleted. A deactivated Guard loses its location assignment. Both need the Admin's own
   password (no OTP).
3. **Supervisors update Guards:** full name, username, phone number, a new password set for the
   Guard, and the Guard's location. Each needs the acting user's own password (the location endpoints
   are unchanged and need none). Email changes stay with the Guard's own verified flow. Supervisors
   cannot see or change Supervisors.
4. **Audit and Admin notifications:** every Staff Management change (details, password, location,
   deactivation, reactivation) writes an `organization_audit_logs` row with field names only. When a
   **Supervisor** acts on a Guard, the campus's active Admins get an in-app notification
   (`GUARD_UPDATED`, `GUARD_PASSWORD_SET`, `GUARD_LOCATION_CHANGED`; `subject_user_id` = the Guard).
   Admins' own actions notify nobody (requirements Section 5).
5. **Two Guard categories, derived from the location assignment (no new column):**
   - **Standby / Quick Response:** no building, floor or area. Kept for campus-wide dispatch and alert
     escalations; receives alerts only through escalation.
   - **Assigned / Stationed:** linked to one building, floor or area; receives the alerts routed there
     automatically (D-013).
   The same account and credentials serve both; an Admin or Supervisor switches the category by
   setting or removing the location.
6. **Guard mobile app (design, not built):** no public registration. The sign-in screen offers the
   operational mode (Standby or Stationed); **the server checks the chosen mode against the Guard's
   current assignment** and refuses a mismatch, then the app opens the Standby Emergency Dashboard or
   the Stationed Monitoring Dashboard. The check is not built yet (the mobile app does not exist);
   `GET /guard/location` already tells the app whether the Guard is assigned.
7. **Not built:** Supervisor detail editing by Admins (Supervisors edit their own profile), staff email
   changes, rejoining, permanent deletion, ID regeneration, an OTP on staff updates, an audit-log viewer.

## D-018 — Guard operational mode: Standby vs Stationed / Standalone, forced sign-out (2026-11-11)

**Requested by the project owner (task of 2026-11-11), refining D-017 item 6:**
1. **Terminology:** **Standby** = no location (the campus-wide escalation pool); **Stationed / Standalone** =
   a building, floor or area. The staff API's category value `ASSIGNED` is renamed `STATIONED` (Phase 8 was
   not approved yet, so no client depends on the old value).
2. **The category is the location, nothing else:** new Guards are created without a location (Standby);
   assigning one makes them Stationed; clearing it (`DELETE /locations/guard-assignments/{guard_id}`) deletes
   the `guard_location_assignments` row and returns them to Standby.
3. **Forced sign-out:** any insert, delete or location-changing update of a Guard's assignment sets
   `user_credentials.sessions_revoked_at` (trigger, migration 0019). The API refuses Guard tokens issued
   before it with `401 ASSIGNMENT_CHANGED`. Tokens now carry an exact issue time (`iat_us`); tokens without it
   are refused for the whole second of a change. Re-setting the same location changes nothing. Staff
   sessions are not affected.
4. **Sign-in:** `operational_mode` (`STANDBY` or `STATIONED`) is required in `POST /auth/guard/login` and
   in the code step. It is compared with the assignment only after the right password (so the assignment
   is never revealed without it) and again at the code step; a mismatch is `409 OPERATIONAL_MODE_MISMATCH`.
   The token response and `GET /guard/dashboard` name the dashboard: `STANDBY_EMERGENCY` (Standby Emergency
   Dashboard) or `STATIONED_MONITORING` (Stationed Monitoring Dashboard).
5. **Forgot password:** `operational_mode` is required with `guard_id`. **Deliberate difference from the
   request:** a mismatch is not reported as an error; it is answered like non-matching details (same `202`,
   no code), because forgot password is unauthenticated and must not reveal whether an account exists or how
   it is assigned (the existing anti-enumeration rule).
6. **Not built:** the Guard mobile app itself; push/WebSocket notice of a forced sign-out (the app learns it on
   its next request). The check uses the database clock against the token's issue time, so API and database
   clocks must agree.

## D-019 — Platform Super Admin management backend (2026-11-12)

**Task of 2026-11-12 (backend only; `super_admin/` untouched):**
1. **Isolation kept:** the platform database role still has no rights on any tenant table. Migration 0020
   adds seven `SECURITY DEFINER` functions granted only to it: counts (`platform_overview`), organization
   metadata with the Main Admin's contact and campus/camera counts (`platform_organization_list`,
   `_detail`, `_campuses`, `_names`), and two changes (`platform_update_main_admin`,
   `platform_set_organization_status`). None returns stream addresses, footage, alerts, chat, reports or
   personnel. The tenant role cannot call them.
2. **Public identifier:** routes and responses use the Organization ID (`organization_id`, the code
   organization users already see), never internal UUIDs.
3. **Sensitive actions** (Main Admin contact, status, AI settings) need the Super Admin's current database
   password, through the same check as credential changes: wrong passwords count toward the login lock and
   are audited as `FAILURE`.
4. **Status enforcement:** non-`ACTIVE` organizations were already refused at sign-in; `get_current_actor` now
   also refuses every tenant request of their users (`403 ORGANIZATION_INACTIVE`), so routes without their own
   status check (camera list, `/auth/me`, campus requests, the Admin directory) are covered.
5. **Main Admin edit** changes full name, email (unique across the platform) and phone, without OTP to the new
   email (the requirement names the Super Admin's password only). The Admin's password and sessions are not
   touched.
6. **AI settings** are one platform-wide row (`platform_ai_settings`): seven module switches and five default
   parameters (confidence threshold, frame sampling, cooldown, retries, timeout). The API marks which modules
   exist in code; no worker reads the settings yet.
7. **Health** reports only what is measured (Core API, PostgreSQL, media storage); services of the planned
   architecture that are not deployed are `NOT_DEPLOYED` and never counted as running. No history, so no uptime
   percentages.
8. **Audit log:** a trigger records `ORGANIZATION_REGISTERED`; viewer with filters (organization by ID or name,
   action, outcome, date range), paging and CSV export (formula-safe, audited). The existing rule stays: erasing
   an organization erases its platform audit rows.
9. **Not built:** Excel/PDF export, an active-session list for the Super Admin, real health of undeployed services,
   AI workers reading the settings, organization addresses (not collected).


## D-020 — "Already Registered Organization?": campus requests from applicants (2026-11-12)

Requirements Section 8; decisions G, I and L of the approved clarifications. Migration 0021.
1. **Applicant:** public `POST /auth/campus-requests` with the organization's **exact name and its Organization ID**
   (both must match the same active organization; stricter than decision G's name alone, and the same pair as
   sign-in), the proposed campus name (compulsory, unique within the organization ignoring capitals), and the
   applicant's full name, username, official email, phone and password; then the email code.
2. **Queue:** only email-verified requests reach the Main Admin (`GET /campus-requests`).
3. **Before approval** the applicant has no account; the right password at sign-in answers `403
   ADMIN_APPROVAL_PENDING`, without a session or code (decision L).
4. **Approval** creates the campus and a working Admin (username, phone and password from the request) in one
   database function; the new Admin manages only that campus. Before D-020 approval created an Admin without a
   password.
5. **Decision window:** 72 hours from email verification; after that the request cannot be approved or used and is
   deleted (decision I). The password hash is deleted as soon as a request is decided.
6. **Still open:** decision H (password + fresh code to approve), full deletion on rejection, tombstones (J).

Why the name and ID cannot drift between submission and verification: the Organization ID is permanent (a
database trigger refuses changes) and no route renames an organization, so the organization matched at submission
is the one verified; the code step re-checks that it is still active.

## D-021 — Compulsory first-campus name at organization registration (2026-11-13)

Amends decision E ("Main Campus", no campus field). Migration 0022.
1. `POST /auth/organization-registrations` requires `campus_name` (2–100 characters, letters or digits; spaces
   collapsed). It becomes the name of the organization's first campus; the Main Admin belongs to it.
2. Registrations still pending when the migration runs keep the name they were promised, "Main Campus".
3. The completion answer already carried both IDs; the web app now shows the Organization ID and the Main Admin
   ID with copy buttons and explains that additional campus Admins need the exact organization name and the
   Organization ID.
4. Unchanged: the Organization ID is generated with the organization and is permanent; campus IDs are separate.

## D-022 — ML training pipelines and ONNX serving adapters for weapon, fight and Re-ID (2026-11-15)

Requirements 6, 14, 14C and 20. Built and tested on CPU with synthetic data; **no dataset downloaded and no
model trained yet** (first runs on the RTX 4080/4090 server).
1. **Separation:** `ml_training/` trains, evaluates and exports; `services/cv_inference/` serves ONNX files with a
   manifest (pre-processing, output format, labels, calibrated thresholds, dataset, commit, test metrics,
   SHA256). The runtime refuses a file that does not match its manifest and never imports a training library.
2. **Models:** weapons — YOLO11 (m/l) or RT-DETR-L via `ultralytics` (AGPL-3.0; flagged for commercial use);
   fight — Video Swin-T by default (MViT v2-S, Swin-S, R(2+1)D-18 as options) from torchvision with Kinetics-400
   weights, 16-frame clips; Re-ID — OSNet-x1.0 (implemented here) or ResNet50-IBN-a with a BNNeck, 512-D.
3. **Evaluation rules:** hashed, deterministic splits; thresholds and Re-ID bands chosen on validation data (a
   slice of train, or held-out identities), reported on the untouched test split; false positives per 1,000
   negative frames and false-alarm rate on normal clips are first-class metrics; Re-ID follows the Market-1501
   protocol (same camera and junk removed).
4. **Export:** opset 17, dynamic batch, `onnxsim`, PyTorch↔ONNX parity < 1e-4 at batch 1 and 2; Video Swin's
   shifted-window mask is built as a constant at export (torchvision's version does not trace).
5. **Runtime:** modules switched on independently (`CV_ENABLE_*`), composite detector, per-camera 16-frame ring
   buffer for the clip model, `CV_DEVICE` cpu / cuda / tensorrt / auto (TensorRT → CUDA → CPU, never a silent
   fallback when a device is named).
6. **Re-ID limits:** campus-scoped Faiss gallery with a time window; same camera never matched; bands
   LIKELY_MATCH / UNCERTAIN / NO_RELIABLE_MATCH only, `review_required` always true; observations go to a JSONL
   file because Core API has no Re-ID intake yet; nothing is persisted or sent as an alert.
7. **Rejected:** `pytorchvideo` (does not import with torch 2.x); `torchreid` as a dependency (OSNet is
   implemented, its weights still load).

## D-023 — Video ingestion, live monitoring, recordings, retention and the evidence vault (2026-11-17)

Requirements 13, 17, 18, 21; Admin spec pages 1–3, 8, 9, 23; Supervisor spec pages 1–3, 5, 8, 9. Migration 0023.
Tested with recorded-file sources and a closed local RTSP port; **no real camera has been connected yet**
(requirements 13: not claimed as complete until tested with actual equipment).
1. **Storage tiers** under `CCTV_STORAGE_ROOT` (local disk; no Azure provider yet):
   `recordings/<org>/<campus>/<camera>/<YYYY-MM-DD>/<UTC start>.mp4` (Tier 1, routine, volatile),
   `evidence/<org>/<campus>/<evidence id>/clip.mp4|snapshot.jpg` (Tier 2, permanent),
   `live/<org>/<campus>/<camera>/latest.jpg` (newest frame), `frames/<camera>/` (bounded CV spool). Campus files
   stay at `<org>/<campus>/`. Permanent campus/organization deletion stages and erases every tier.
2. **Ingestion** (`services/video_ingestion`): one ffmpeg process per camera decodes once and writes both the
   MP4 segments (60 s by default, `+faststart`, browser-playable) and a sampled MJPEG stream
   (`INGEST_FRAME_SAMPLE_FPS`, default 5) for the live frame and the CV spool. RTSP/RTSPS only; ONVIF cameras
   stay **UNKNOWN** with the reason, because stream discovery needs device credentials, which the system does
   not store (D-012). `INGEST_FILE_SOURCES` plays a recorded file in real time as a camera (the fallback until
   equipment is connected). Reconnects with back-off; no stream URL appears in logs or reports.
3. **Live view = the newest JPEG about once per second**, fetched with the user's token. Not video-rate
   streaming (HLS/WebRTC is not built); no stream address or credential ever reaches the browser.
4. **Camera status** ONLINE / OFFLINE / UNKNOWN: OFFLINE after `INGEST_OFFLINE_AFTER_SECONDS` (15) without
   frames or when the stream fails, with a generic reason. A change to OFFLINE, and back to ONLINE, notifies the
   campus's Admins and Supervisors (`CAMERA_OFFLINE`, `CAMERA_ONLINE`). Staff can run a **connection test** from
   the API server (RTSP `OPTIONS` / ONVIF HTTP `HEAD`, no credentials), audited.
5. **Retention**: per **campus** (every user works inside one campus, D-001; an organization-wide value would need
   cross-campus writes no role has), value + unit in **Minutes, Hours or Days**, 1 minute to 365 days. **Admins
   and Supervisors** change it (requirements 17; Supervisor spec page 8 item 6), each confirming with **their own
   password** (sign-in lockout counts wrong passwords); audited with old/new value and role. Until a campus sets
   one, `RECORDING_RETENTION_DEFAULT_DAYS` (7) applies — a deployment setting shown as "server default", not a
   fixed rule. The timeline shows each segment's purge time.
6. **Retention daemon** (`services/storage_retention_daemon`, every 300 s): the Core API decides what expired;
   the daemon unlinks only paths that resolve inside `recordings/`, refuses anything else, and never lists or
   opens `evidence/`. It reports disk use; at `STORAGE_WARNING_PERCENT` / `STORAGE_CRITICAL_PERCENT` (80 / 90,
   shared with the platform health page, D-019) the **Admins and Supervisors** of every active campus get
   `STORAGE_WARNING` / `STORAGE_CRITICAL` once per level change (again after 24 h at the same level). Thresholds
   are server settings: per-campus thresholds (Admin spec page 23 item 4) are not built, since one disk is
   shared by all campuses of a node.
7. **Evidence vault**: Admins and Supervisors save clips and snapshots from an **alert** (10 s before the
   detection to 20 s after its last repeat, clipped to what was recorded; the detector's snapshot is reused when
   it exists) or from **playback** (mark in/out, at most 300 s; or one frame), and edit title, notes and a free-text
   **investigation reference** (the Incidents module is not built; the alert link is kept as a foreign key).
   Files carry SHA-256 and size. Evidence never expires. **Not automatic:** the architecture draft (Part 5) copied
   footage into the vault for every alert; a permanent tier filled with every unreviewed detection, false alarms
   included, would grow without limit, so staff save what an investigation needs, and an alert's footage stays in
   the routine recordings until the campus retention expires.
8. **Evidence deletion — resolves the Section 22 item "confirmation process":** an Organization Admin deletes,
   confirming with their own password (the web page also asks to type DELETE); the record and the audit entry
   (with the title) are committed first, then the files are erased; a file that cannot be erased is reported,
   never hidden. **Supervisor boundary (provisional, needs the project owner's confirmation):** requirements 17
   allow an "authorized Supervisor", but no way to authorize a Supervisor exists yet, so **no Supervisor can
   delete evidence** (API and Row-Level Security). A per-Supervisor grant can be added without changing the rest.
9. **Audit** (organization audit log, no viewer yet): `LIVE_VIEW_STARTED` (once per page or fullscreen, not per
   frame), `CAMERA_HEALTH_CHECKED`, `RECORDING_PLAYED`, `RECORDING_RETENTION_CHANGED`, `EVIDENCE_SAVED`,
   `EVIDENCE_VIEWED`, `EVIDENCE_UPDATED`, `EVIDENCE_DELETED`. No URL, password or token is recorded.
10. **Isolation**: all new tables are campus-scoped with Row-Level Security; Guards get 403; another campus's
    camera, segment or evidence answers 404; the platform (Super Admin) role has **no privilege** on any new table
    or function. The services use a separate `INTERNAL_SERVICE_TOKEN` (header `X-Service-Token`) and reach data
    only through narrow SECURITY DEFINER functions.
11. **AI on the live page**: badges come from the camera's **open alerts** (weapon, fight / violence, restricted
    area; red border for high priority). The page does not claim that analysis is running. Detection boxes,
    track IDs, confidence and timestamp overlays need per-frame detections the CV service does not publish yet.
    `cv_inference` can now read the ingestion spool (`--spool-dir`), so live cameras are analysed without
    opening the stream twice.

## D-024 — Remaining Organization Admin and Supervisor web features (2026-11-18)

Org Admin spec pages 1, 4, 6–9, 11–13, 15–19 (Live items), 17, 21, 22, 24, 25; Supervisor spec pages 1, 4, 6, 7, 14,
17, 18; requirements 8, 14–18, 21. Migrations 0024–0029. Every open point below was decided explicitly; items marked
**provisional** need the project owner's confirmation.

1. **Super Admin tokens on organization routes answer 403 `SUPER_ADMIN_NOT_ALLOWED`** (was 401): the token is valid,
   the role is not allowed. Applies to every Admin / Supervisor route, old and new.
2. **Campus requests (page 15) — decision H partly adopted.** Approval needs the **Main Admin's password and an emailed
   code** (a step-up grant, consumed once, `app_step_up_consume`); **rejection needs the password only** (it creates
   nothing). The grant is not bound to one request: it is single-use and short-lived, which is enough for one decision.
   Only the Main Admin sees the page and the API (`MainAdminDep`; other Admins 403). **Decision I:** a rejected request
   keeps no applicant details — name, email, username, phone and campus name become NULL in the same statement
   (`details_erased_at`); history shows "erased". Pending requests past the decision window are hidden and cleaned up.
3. **Audit logs (Admin 21 / Supervisor 17).** Records stay insert-only (no UPDATE/DELETE for anyone, including the
   viewer). A stored `category` (16 values) and `outcome` (SUCCESS / FAILURE / DENIED) are computed by the database.
   **Admins** read every record of their campus; **Supervisors** read the operational categories (alerts, Guard
   responses, cameras and locations, AI settings, evidence, video, person search, manual reports, incidents and case
   chat) **plus their own records** — never user-management, settings or other people's sign-ins. Super Admin: no
   access (RLS + 403). New audited events: sign-in succeeded / failed / blocked, password reset requested / completed,
   camera and location create / update / delete / link, campus-request approve / reject, exports. CSV export is audited
   and capped at 50 000 rows; cells are formula-safe.
4. **Incidents & Investigations (Admin 6 / Supervisor 6).** Admins and Supervisors open and manage cases; Guards only
   take part in the case chat (invited, or recipients of a linked alert). Status flow Open → Under Investigation →
   Responding → Resolved / Dismissed; any active status may move to another; a closed case can only be reopened (to
   Under Investigation); resolving, dismissing and reopening need a note. Nothing is deleted; names are copied so the
   record survives removed accounts and cameras. Each case lists footage gaps (no / partial recording, camera
   offline / removed) instead of implying completeness. **Not built:** the AI investigation summary, policy
   retrieval, recommendations and approval checkpoint (page 6 items 9–14) need the LangGraph agent and RAG service.
   Evidence can belong to at most one case. Fixed a D-023 bug found here: the composite foreign keys of
   `evidence_items` used `ON DELETE SET NULL` on all columns (nulling the tenant columns); now
   `SET NULL (alert_id)` / `(camera_id)` only.
5. **Alert operations (Admin 4 / Supervisor 4).** A **handler** (any active Admin or Supervisor of the campus) can be
   set on an open alert; the handler is notified. **Automatic escalation:** an alert no Guard acknowledged within the
   organization's time (defaults **HIGH 120 s, STANDARD 300 s**) reaches every active Guard of the campus; Admins and
   Supervisors are told even if no Guard is left to add (a background task in the API, every
   `ALERT_ESCALATION_POLL_SECONDS` = 15). **Assistance broadcast:** when a Guard answers "Additional Guards
   Required", the alert also reaches the campus's Standby Guards and the Stationed Guards of the same building (a
   deferred constraint trigger, so it follows the assessment). Both can be switched off per organization. Staff do not
   "acknowledge" alerts (Guards do, D-013): a Supervisor's dashboard "Acknowledge Alert" shortcut opens the newest
   pending alert, where taking it ("I'll handle it"), escalating and sending chosen Guards happen.
6. **Organization Settings (page 25):** every Admin reads; **only the Main Admin** changes official phone, address,
   time zone, date / time formats and the alert defaults (escalation times 30–3600 s / 30–86400 s, broadcast on/off),
   with their password; audited. The official email is fixed (the verified registration address). **Billing is not
   built** (out of scope).
7. **AI Configuration (Admin 17 / Supervisor 14) — three levels:** platform (Super Admin) ⊇ campus (**Admins**, with
   password) ⊇ camera (**Admins and Supervisors**, with password). Campus defaults: every module on **except face
   recognition**. Vision workers read the effective modules (`/internal/cameras/{id}/ai-config`) and report what they
   run (`ai-status`). **Live overlays:** a worker writes `live/<org>/<campus>/<camera>/overlay.json` (boxes as frame
   fractions, track ID, confidence, module); the page draws it only while it is recent and says "no recent data"
   otherwise. Modules without a trained model (weapon, fight, Re-ID) run only when their model file is installed.
8. **Person Search (Admin 7 / Supervisor 7).** Search by reference photo (**needs the trained Re-ID model on the
   server**; without it the page says so) or by an observed person. Bands: similarity ≥ **0.75** LIKELY_MATCH,
   ≥ **0.55** UNCERTAIN, weaker hidden; at most **50** results; period ≤ 31 days (default 24 h). Only a LIKELY_MATCH
   can be marked VERIFIED_MATCH, and even that is a reviewed resemblance, never an identity. Each search stores its
   limitations (camera offline, Re-ID off, no observations). Observations (pgvector 512-d + crop) are accepted only
   from cameras allowed to run Re-ID and are purged with the campus's recording retention. A result's crop can be
   saved as evidence (source PERSON_SEARCH) and a verified result linked to a case.
9. **Evidence polish (pages 8–9):** up to **10 tags** per item (1–40 letters, digits, spaces, dots, dashes;
   duplicates ignoring capitals dropped); the camera's **location is copied** when the item is saved; filters by tag,
   location and case; a case can be chosen when saving. The alert page shows the detection snapshot (or the recorded
   frame at that time) and plays the surrounding clip **for viewing only** (nothing is saved; clip viewing audited).
10. **Profiles and IDs (pages 11–13, 24):** the Admin profile gains a phone number (changes confirmed with the emailed
    code as for Supervisors). **Supervisor IDs stay permanent** (migration 0008). Guard IDs can be regenerated by an
    Admin or Supervisor with their password (Guard and, when a Supervisor acted, the Admins are notified).
    **Rejoining is Admin-only** (D-017), password-only, and reactivates the same account (no duplicate).
    **Permanent deletion** (Admins, former staff only, password + typed DELETE) erases personal data and replaces the
    name copies with "Deleted user"; the anonymous account stays so alerts, cases and audit records remain linked.
11. **Reports & Analytics / Dashboard (Admin 1, 22 / Supervisor 1, 18):** periods Today, 7 days, 30 days or custom
    (≤ 366 days) in the organization's time zone; building / floor / area filters; CSV and PDF exports (audited).
    Camera uptime comes from recorded state changes; time before a camera's first known state is "unknown", never
    counted as up or down. The dashboard feed covers the last 7 days and refreshes every 30 s.
12. **Not built / honest limits:** AI agent items (page 6 items 9–14); trained weapon / fight / Re-ID models (GPU
    training excluded from this work — photo search and those detectors stay unavailable until model files are
    installed); no physical camera has been connected; billing; per-campus storage thresholds (D-023).

## D-025 — Guard mobile app, real-time signals, in-app voice calls, persistent alert alarm, push tokens (2026-11-19)

**Requested by the project owner (task of 2026-11-19):** build the Guard mobile app in `mobile/` for every documented
screen; add real-time chat over a WebSocket with a polling fallback; add in-app voice calls with LiveKit for Guards,
Supervisors and Admins (backend role-compatible now, web UI in a later phase); add a persistent loud alert alarm that
cannot be missed; add device push-token registration ready for FCM. Admin / Supervisor web UI for calls is **not** part
of this decision's work (scheduled next). Migration 0030.

1. **Real-time = signals, not content.** Triggers send `pg_notify('cctv_realtime', json)` with an event name and IDs
   only (`alert.assigned`, `alert.updated`, `alert.message`, `incident.message`, `incident.updated`,
   `incident.invited`, `incident.removed`, `notification`, `call.invited`, `call.updated`, `session.revoked`,
   `organization.status`). Each API process LISTENs once and forwards a signal only to its connections allowed to see
   the target (staff of the campus; Guards checked with `app_comm_target_visible` under their own RLS binding). Clients
   then read the content over REST, so every existing permission rule still decides what anyone sees. Message text,
   names and tokens never travel on the socket or through `pg_notify`. **No Redis or RabbitMQ is needed**; delivery is
   best effort and clients keep a polling fallback (5 s while the socket is down, 30 s as a safety net).
2. **WebSocket authentication.** `WS /api/v1/realtime/ws`; the access token is sent in the first message
   (`{"type": "auth", "access_token"}`) within `REALTIME_AUTH_TIMEOUT_SECONDS`, never in the URL (access logs). The
   token is checked with exactly the rules of every REST route (one shared function, `authenticate_tenant_token`):
   Super Admin tokens `4403 SUPER_ADMIN_NOT_ALLOWED`, inactive organization `4403 ORGANIZATION_INACTIVE`, Guard
   assignment changed `4401 ASSIGNMENT_CHANGED`, password changed / expired `4401`. Every heartbeat (`ping`) checks
   the session again, and `session.revoked` / `organization.status` signals end affected connections **at once** —
   this closes the gap D-018 item 6 left open ("an idle app learns it on its next request").
3. **Voice calls (LiveKit).** `VOICE_CALL_PROVIDER=none|livekit`; the API signs short-lived participant tokens (HS256,
   API key as issuer, `LIVEKIT_TOKEN_TTL_SECONDS`, default 900) for **audio only** (`canPublishSources: ["microphone"]`,
   no data channel) and best-effort closes the room (`RoomService.DeleteRoom`) when a call is ended. No audio passes
   through the Core API. With `none`, starting or joining answers `503 VOICE_CALL_NOT_CONFIGURED` and nothing is
   written. Rules (same for every role):
   - **Who:** anyone who may see the alert or case — staff of its campus, a Guard recipient of the alert, a Guard taking
     part in the case (invited or recipient of a linked alert). Other campuses 404; Super Admin tokens 403.
   - **Start:** only on an open alert / case (`409 ALERT_CLOSED` / `INCIDENT_CLOSED`); at most one active call per
     alert and per case (a partial unique index); starting while one is active **joins** it (`200 JOINED`, else
     `201 STARTED`).
   - **Ringing:** a new call invites the campus's active Admins and Supervisors and the target's Guards (not the
     caller). Anyone allowed may also join uninvited. Invitations ring over the WebSocket (`call.invited`) and through
     `GET /calls` (polling). **Calls do not send push notifications** (only alerts do).
   - **End:** Admins, Supervisors and the person who started a call end it for everyone; others leave
     (`403 CALL_END_NOT_ALLOWED`). A call also ends when the last participant leaves (`EVERYONE_LEFT`; ringing people
     become `MISSED`) and when its alert or case is resolved or dismissed (`TARGET_CLOSED`, a trigger).
   - **Audit:** `ALERT_CALL_*` / `INCIDENT_CALL_*` (STARTED, JOINED, DECLINED, LEFT, ENDED) with `{call_id}`; never a
     token. These fall in the existing operational audit categories (Guard response / Alerts / Incidents), so
     Supervisors see them too.
   - **Names are not copied** into call tables (read from `users`), so permanent deletion's "Deleted user" applies.
4. **Responder roster for Guards** (Guard screen 6 item 6, requirements 15.4). Guards could only read their own
   recipient row (D-013 RLS). `GET /alerts/{id}/responders` (function `app_alert_responders`) shows staff and the
   alert's Guard recipients who acknowledged or are responding: name, state, assessment, reason, times, `is_me`.
   **Guard IDs are not shown to Guards** (a Guard ID is a sign-in identifier).
5. **Push tokens.** `POST /guard/device-tokens {token, platform: ANDROID|IOS, provider: FCM|APNS, app_version?}` and
   `DELETE /guard/device-tokens/{token}` (own only). A token belongs to one account at a time (moved by
   `app_device_token_register`); at most 10 per account; deleted when the account stops being ACTIVE. Tokens are never
   returned or audited. `PUSH_DELIVERY_MODE=none|fcm`: with `fcm` a background task claims unsent, unread alert
   notifications (`app_push_claim`, one process per notification via `push_claimed_at`, younger than
   `PUSH_MAX_AGE_SECONDS`) and sends FCM HTTP v1 high-priority messages on the `security-alarm` channel. **Pushes carry
   no names, places or text** — only the kind and IDs — because they pass through Google's servers. Dead tokens are
   forgotten. **Only Android FCM tokens are pushed**; iOS APNs tokens are stored but not delivered (APNs not built).
   Live FCM delivery has **not** been tested (needs a Firebase project and a development build).
6. **Persistent alert alarm (mobile).** While an open alert sent to the Guard is still `NOTIFIED` for that Guard, a
   bundled siren loops at full volume (also in silent mode and in the background) with a repeating vibration, and a
   full-screen alarm covers the app. It stops **only** when the server confirms "Acknowledge Alert" or "I'm Responding"
   (escalated, automatic and assistance alerts show "I'm Responding" first), when the alert is closed, or when the
   session ends. Tapping outside and the Android back button do nothing; "View details" shrinks it to a red banner and
   the siren keeps ringing. An offline acknowledgement is **not** queued for the alarm: it keeps ringing until the
   server confirms (rule chosen to match "cannot be missed"). A high-importance Android channel (`security-alarm`,
   siren, DND bypass where allowed, lock-screen visible, alarm audio usage) is created; a loud local notification is
   posted when an alert arrives while the app is in the background. Profile & Settings has a 3-second alarm test.
7. **Guard profile context.** `GET /guard/profile` adds `campus_name`, `status` and `operational_mode` (screen 10);
   nothing else changed in existing contracts. The app still never sends `organization_id` or `campus_id`.
8. **Mobile app decisions:** Expo SDK 58 + Expo Router + TanStack Query; token only in `expo-secure-store`
   (`WHEN_UNLOCKED_THIS_DEVICE_ONLY`); the remembered identifier (`guard_id`, `email`, `operational_mode`) only when the
   Guard asks, never the password; the reset token is kept in memory only. The offline outbox stores photos as files
   (AsyncStorage is small on Android) and tags each report with the Guard ID that wrote it, so a different Guard on the
   same phone never submits it. Alert actions and chat messages are not queued offline (they need a connection).
   Snapshots / clips stay staff-only (D-023): the alert screen says so instead of trying. Colour palette sampled from the
   owner's reference designs only; screens and flows come from the documentation.
9. **Not built / not verified:** the Admin / Supervisor web UI for calls and for the WebSocket (next phase; the backend
   is ready — **built since D-026**); a physical handset test; a live LiveKit room; live FCM delivery; APNs; call push; video calls; access
   tokens still have no refresh (a Guard signs in again after `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`). Background JavaScript
   on phones is limited by the OS: a locked or killed phone is woken only by FCM push once configured.

## D-026 — Real-time messaging and in-app voice calls in the Admin & Supervisor web app; Web ↔ Mobile parity (2026-11-19)

**Requested by the project owner (task of 2026-11-19):** implement the real-time WebSocket and LiveKit voice calls of
D-025 inside the Organization Admin & Supervisor web app (`website/`), verify end-to-end synchronization between the
Guard app (`mobile/`), the web app and `core_api`, and update the documentation for Web ↔ Mobile parity.
`super_admin/` and `backend/docs/requirements/` unchanged; every mobile contract kept (additive fields only).
Migration 0031.

1. **Backend audit.** Already correct and now covered by tests (`test_realtime_web_parity.py`): the WebSocket accepts
   Admin and Supervisor tokens with campus isolation (other campuses receive nothing); Super Admin tokens are refused
   (`403` REST, `4403` socket); every call route, including "end for everyone", works for Admins and Supervisors. Four
   gaps were fixed:
   - **Case changes other than status were silent.** `incident.updated` only fired when `incidents.status` changed,
     so a web page could not show a colleague's case note, link or assignment live. Migration 0031 replaces
     `trg_rt_incidents` with `trg_rt_incident_events` (`AFTER INSERT ON incident_events`): **one `incident.updated` per
     case history entry**, with `kind` (the history kind: `NOTE_ADDED`, `STATUS_CHANGED`, `ASSIGNED`, `ALERT_LINKED`,
     `DETAILS_UPDATED` …) and `state` (the new status, only on status changes). Still IDs only. Invitations keep
     `incident.invited` / `incident.removed`. The Guard app already re-reads a case on any `incident.updated`, so the
     extra signals and the new optional `kind` field need no app change.
   - **Ringing calls had no context.** `CallSessionResponse` adds `target_title` ("Weapon alert · Gate Camera",
     "INC-000007 · Weapon at the lobby") and `target_location` ("Admin Block → Ground → Lobby" or `null`), read with
     the call (alert type and camera / case number and title; the alert's or case's building, floor and area).
     Additive: the Guard app types mark them optional and its incoming-call card and call overlay now show them.
   - **The dev proxy did not forward WebSocket upgrades.** `website/vite.config.ts` proxies `/api` with `ws: true`. In
     production the reverse proxy must forward `Upgrade` for `/api/v1/realtime/ws` (not tested here).
   - **The web app ignored `403 ORGANIZATION_INACTIVE`.** The API client now ends the session on `401` and on `403`
     with code `ORGANIZATION_INACTIVE` (other `403`s, e.g. `CALL_END_NOT_ALLOWED`, do not sign out), and the sign-in
     page states why (organization inactive / session ended).
2. **Web real-time engine** (`website/src/realtime/`). One WebSocket per browser tab, opened by the signed-in shell, with
   the Guard app's protocol: token from `sessionStorage["cctv.accessToken"]` sent as the first message (never in the
   URL), `ping` every `heartbeat_seconds`, reconnect with exponential back-off (1 s, 2 s, 4 s … 30 s; reset after
   `ready`; at once when the browser comes back online). `session_ended` or a bare close `4401` / `4403` signs out with
   the server's code. Signals never carry text: each page re-reads only what changed (`useLiveRefresh(reload, match,
   {debounceMs, fallbackMs})`), and after a **re**connection a single `RESYNC` makes every live view re-read once
   (signals sent while the socket was down are lost). While the socket is not open, live views poll instead:
   | View | Signals | Polling while down |
   |---|---|---|
   | Alert details (Guard acknowledgements, assessments, I'm Responding, responder counts, escalations, status) | `alert.updated` / `alert.assigned` for the alert | 15 s |
   | Alert chat | `alert.message` for the alert | 10 s |
   | Case details (notes, status, links, assignment, Guards in the chat) | `incident.updated` for the case | 20 s |
   | Case chat | `incident.message` for the case | 10 s |
   | Alerts list / Incidents list | `alert.updated`, `alert.assigned` / `incident.updated` (debounced 0.8 s) | 30 s |
   | Dashboard feed and alert / activity panels | `alert.updated`, `alert.assigned`, `incident.updated`, `notification` (debounced 1.5 s) | feed every 30 s (existing) |
   | Topbar bell / Notifications page | `notification` | bell every 60 s (existing) / Refresh button |
   | Voice-call card of an alert / case | `call.invited` / `call.updated` for it | 15 s |
   | Ringing calls (`GET /calls`) | `call.invited` / `call.updated` | 5 s (30 s while live) |
   Manual Refresh buttons stay. The topbar shows the honest state: **Live** (socket open), **Connecting… /
   Reconnecting…**, or **Polling** (socket closed).
3. **Web voice calls** (`website/src/features/calls/`), the same rules and states as the Guard app:
   - `livekit-client` sits behind a `VoiceClient` adapter and is loaded with a dynamic import in its own lazy chunk
     (~580 kB, ~150 kB gzipped; not downloaded until a call connects). Tests use a fake adapter.
   - **Start voice call / Join active call** card on Alert details (Admins and Supervisors) and on Case details, with a
     banner while a call is going on (who is in it, with roles). Starting while a call is active joins it.
   - **Incoming call** on every page: caller name and role badge, the alert or case (linked) with its location, how many
     are in the call, Decline / Join. A soft two-tone ringtone (Web Audio, every 2 s) plays while it rings; when the
     browser blocks sound until the first click, the banner says so.
   - **Call dock** (bottom right, never blocks the page; the page gets room to scroll clear of it): link to the target,
     duration timer, connected count, mute / unmute, deafen, leave, participant list with role badges and a speaking
     indicator, and **End call for everyone** when the server says `can_end` (Admins, Supervisors, the starter).
   - **Honest notices:** server without a provider (`voice_calls_available: false`, or `503 VOICE_CALL_NOT_CONFIGURED`)
     → Start disabled with "not configured (VOICE_CALL_PROVIDER=none)"; browser without a secure context, WebRTC or a
     microphone → why it cannot call; **microphone denied** → stays in the call to listen, muted, with the reason;
     audio autoplay blocked → "Play call audio"; ended by someone else / everyone left / alert or case closed → the
     reason. If the audio cannot connect, the browser leaves the call on the server (never "in" a call without audio).
   - One call at a time per tab. If the same account joins the call from another tab or device, LiveKit drops the
     older connection (one connection per identity); that tab says so and does **not** leave the call on the server.
   - Signing out (or the session ending) leaves the active call first, while the token is still valid.
4. **Verification.** core_api 730 passed, 0 failed (725 before), including `test_realtime_web_parity.py` (5 tests: staff sockets follow Guard actions, both chats,
   case notes and status; an Admin-started call rings the Guard with the target, a Supervisor ends it for everyone and
   the Guard hears `ENDED`, the other campus hears nothing; a Guard-started case call rings the web; an assignment
   change ends only the Guard's socket; migration 0031 round trip). Web: 31 Vitest tests in `src/test/d026.test.tsx`.
   Live cross-client smoke `website/smoke/realtime_comms_smoke.py`: 45 checks with a Guard, a second Guard, an Admin,
   a Supervisor and another campus's Admin connected at once through the Vite proxy against a scratch database. The
   Guard app's 68 tests and type check still pass. Screenshots: `FYP_Documentation/screenshots/web-realtime-comms/`.
5. **Not verified in real life:** live LiveKit audio between a browser and a phone (no LiveKit server or keys here; a
   local LiveKit server could not be run in this environment), so tokens, rooms and the call lifecycle are verified but
   not media — the active-dock screenshots use a stand-in for the LiveKit module; Firefox and Safari (headless Chrome
   only); a production reverse proxy forwarding WebSocket upgrades; a physical handset. No web push notifications
   (out of scope; the bell updates live while a tab is open).

## D-027 — Read-only, database-driven AI Investigation & Security Analytics Chatbot (2026-11-19) — BUILT & CPU-TESTED (RTX 5080 QLoRA fine-tuning and a live Ollama run pending the GPU workstation)

**Requested by the project owner (2026-11-19):** revise and simplify the AI chatbot / investigation assistant before any
implementation. Documentation only; no code, migration or test was written. Implementation waits for the owner's
approval.

1. **Replaces** the "RAG-Based Security Assistant" (SOP / PDF knowledge base) and the "Agentic Incident Investigation"
   (LangGraph approval-to-execute agent) with **one read-only AI Investigation & Security Analytics Chatbot**
   (requirements Section 4 Layers 2–3, Sections 14A / 14B / 14C; Admin pages 19–20, Supervisor pages 15–16, incident
   page items 9–14). The originals in `backend/docs/requirements/` are unchanged.
2. **Removed from scope:** every file upload (SOP / PDF / manual), Knowledge Base document management, chunking,
   indexing status, document deletion, document embeddings; policy retrieval and policy-grounded recommendations; any
   AI-proposed or autonomous state-changing action (status updates, notes, assignments, broadcasts, messages); the human
   approval-to-execute checkpoint and action execution, with their idempotency controls.
3. **Data:** only the campus's real `core_api` records — alerts (history, recipients, escalations), incidents / cases
   (history, notes, links, case chat), alert chat, manual security reports, cameras with online / offline events and
   uptime, the location hierarchy, Guard assignments and response metrics, evidence metadata and tags, Re-ID
   observations and person-search reviews, audit logs. Every new record is queryable as soon as it is stored.
4. **Capabilities (strictly these six areas, Admins and Supervisors):**
   1. *Time-based trends and historical comparisons* — totals for any year, month, quarter, week or custom range; peak
      hours and days; month-over-month / year-over-year comparisons; the most frequent type (Weapon, Fight / Violence,
      Restricted Area, Guard manual reports).
   2. *Location and camera hotspot analysis* — buildings, floors, gates, areas with the most incidents or alerts; cameras
      that went offline most often; cameras with missing footage during an incident.
   3. *Deep incident investigation* — full chronological case timeline (alert arrival, Guard acknowledgement and
      response, key chat updates, notes, evidence saved); related events near the same time (e.g. ±30 minutes) in the
      same building or adjacent cameras (alerts, manual reports, Re-ID observations); missing-evidence / gap check
      (camera offline, no clip or evidence saved, unverified Re-ID match pending).
   4. *Guard response and operational performance* — average acknowledge, response and resolution times; counts of
      "Additional Guards Required" and automatic escalations (120 s / 300 s); locations with the most unacknowledged or
      escalated alerts (Standby vs Stationed activity).
   5. *Evidence and manual-report search* — manual-report summaries for a location and period; Evidence Vault items by
      tag, camera, location or date with their linked cases.
   6. *Follow-up conversational investigation* — session context for drill-down questions; New Chat / Clear Session
      History; clickable references to the exact alerts, `INC-…` cases, cameras, evidence items and manual reports used.
5. **Grounding and safety:** exact counts, averages, comparisons and timelines come only from permission-checked database
   / investigation tools, never from the LLM; missing or unavailable data is stated explicitly. The chatbot never
   declares anyone guilty and never treats an uncertain Re-ID match as a confirmed identity. Strictly read-only.
6. **Access:** Organization Admins (full own-campus scope) and Supervisors (operational scope only: no Admin-private
   notes, Admin-only audit logs or Admin-only personnel details), enforced by the existing tenant / campus RLS.
   **Guards and the Platform Super Admin: `403`.** UI: a dedicated **AI Investigation Chatbot** page and **"Investigate
   with AI"** on Incident Detail.
7. **Audit:** every chatbot query and every database / investigation tool call is written to `organization_audit_logs`,
   never with passwords, OTPs, tokens or secrets.
8. **Implementation (2026-11-19, migration 0032):** permission-checked structured tools plus a configurable LLM provider
   (`AI_LLM_PROVIDER`) — see items 11–16; an offline evaluation / fine-tuning pipeline under `backend/ml_training/chatbot`
   (as in D-022), to be trained / evaluated on the university GPU server.
9. **Owner's answers (2026-11-19) to the open points** — recorded here and built:
   - **Supervisor data boundary (parity with D-016 / D-024), own campus only.** Allowed: alerts (escalations,
     responses, assessments, handler, alert chat), incidents (cases, history, notes, case chat, linked alerts, reports,
     evidence and verified person-search results), manual reports and review status, cameras with `camera_status` and
     `camera_status_events`, buildings / floors / areas, Guard assignments and response metrics (never passwords, codes
     or sign-in secrets), evidence metadata and tags (read-only), Re-ID observations and person searches (review only,
     never a confirmed identity), and audit records of the operational categories plus the Supervisor's own records.
     **Never queried or surfaced for Supervisors:** personnel and `personnel_*`, restricted-area access notes and revoked
     access history (D-016), user records / rejoining / permanent-deletion history (`user_status_history`), Admin-only
     audit records (other users' sign-ins, password resets, staff creation / deactivation, organization settings,
     campus approvals), campus requests, and organization settings beyond the escalation thresholds and time zone.
     **Admins:** full operational and administrative scope of their own campus (RLS; never passwords, codes, tokens or
     other campuses). **Guards and the Platform Super Admin:** `403` on every chatbot route; the platform database role
     has no privilege on the chat tables.
   - **LLM provider and privacy.** `AI_LLM_PROVIDER=deterministic|ollama|openai_compatible`: `deterministic` (default
     for development, CI and tests; offline, no keys, no downloads); `ollama` (production target: Qwen2.5-7B-Instruct
     `qwen2.5:7b`, optional `qwen2.5:14b`, on the RTX 5080 — nothing leaves the server); `openai_compatible` (vLLM,
     SGLang or an external endpoint via `.env`). Never sent to a model: video, frames, snapshots, passwords, codes,
     tokens, emails, phone numbers or addresses — only labels, figures, times, place / camera names and case references.
   - **Chat retention.** Sessions stay per user and campus until the user deletes them; sessions inactive for more than
     `AI_CHAT_RETENTION_DAYS` (30) are purged by the storage retention daemon; all chats of a campus are erased with the
     campus (D-008 / D-011), and a user's chats are deleted when their personal data is permanently deleted (D-024).
10. **Status:** built and CPU-tested on 2026-11-19 (items 11–16). Not done: the QLoRA fine-tuning run on the RTX 5080
    and a live Ollama model (no weights were downloaded); real-world accuracy on real campus questions.
11. **Engine (`backend/services/ai_rag_and_agent`, package `assistant_engine`).** The empty RAG / LangGraph / approval
    placeholders were removed. Pure Python, imported in-process by core_api (`pip install -e`), so every tool runs in
    the caller's own request transaction under RLS. 13 read-only tools for the six areas (`trend_summary`,
    `peak_times`, `compare_periods`, `location_hotspots`, `camera_reliability`, `case_timeline`, `related_events`,
    `evidence_gaps`, `guard_response`, `audit_activity`, `manual_report_search`, `evidence_search`, `case_list`); a
    deterministic planner with periods in the organization's time zone and follow-up context (period, place, top place
    of the last answer, case, record type); guardrails before any model (accusation / identity, read-only, uploads,
    excluded data); a grounded synthesizer (text, metrics, tables, bars, timelines, citations, explicit "no records");
    with a model, the plan is validated against the catalogue and the vocabulary, and a rephrasing is used only if it
    adds no number and makes no accusation (otherwise the grounded text stays); an unreachable model is reported and
    the answer still comes from the same records.
12. **core_api.** Migration 0032: `ai_chat_sessions` and `ai_chat_messages` (RLS: owner only; `context_incident_id`
    SET NULL; messages also keep `blocks` and `context` for follow-ups), `app_ai_chat_purge`, a trigger deleting a
    permanently deleted user's chats, and the audit category `AI_ASSISTANT` (not operational: Supervisors see only
    their own). Routes `/api/v1/assistant/status|sessions|sessions/{id}|sessions/{id}/messages|incidents/{id}/investigate`
    and `POST /internal/assistant/purge-expired`. Audit: `ASSISTANT_QUERY` (redacted question, tools, provider,
    refusal, latency), `ASSISTANT_TOOL_CALL` per tool (name, labels-only arguments, outcome, rows, latency),
    `ASSISTANT_SESSION_DELETED`. Settings `AI_LLM_PROVIDER`, `AI_LLM_BASE_URL`, `AI_LLM_MODEL`, `AI_LLM_API_KEY`,
    `AI_LLM_TIMEOUT_SECONDS`, `AI_LLM_ALLOW_EXTERNAL`, `AI_CHAT_RETENTION_DAYS`.
13. **storage_retention_daemon:** each pass calls the purge route (`chat_sessions_removed` in its report).
14. **Web:** `/assistant` (nav "AI Investigation", Admins and Supervisors) — chats sidebar with New chat and Delete
    (confirmed), starter prompts, answers with blocks, clickable references, "Tools & queries used", a missing-data
    banner, the decision-support note and an honest provider badge / unreachable-model notice; "Investigate with AI" on
    Incident Detail with a link to continue in the chat. No upload control exists anywhere.
15. **ml_training/chatbot:** configs `chatbot_qwen25_7b_qlora.yaml` (primary) and `chatbot_qwen25_14b_qlora.yaml`;
    dataset builder (planner, follow-up, refusal, safety, missing-data, phrasing; hashed splits; PII-scrubbing log
    exporter), QLoRA training (PEFT + TRL, completion-only loss, best checkpoint by validation loss), evaluation
    (tool selection, argument, follow-up, refusal, safety, numeric faithfulness / completeness, citation grounding,
    missing-data refusal) and Ollama export; runbook for the RTX 5080 (PyTorch with CUDA 12.8+ for Blackwell `sm_120`).
    Deterministic baseline on the synthetic 600-record test split: tool selection 0.925, arguments 0.914, follow-ups
    0.933, every safety and grounding metric 1.0.
16. **Verification:** core_api 743 passed, 0 failed (730 before); engine 32; daemon 7; ml_training 39; web 383 (see
    `implementation_log.md`); live smoke
    `website/smoke/chatbot_smoke.py` 27/27; screenshots in `FYP_Documentation/screenshots/ai-chatbot/`).


## D-028 — Explicit per-camera AI feature selection (default OFF), bulk 1-click assignment, Supervisor AI feature visibility, Super Admin per-organization AI blocks, true per-camera model skipping (2026-11-24) — BUILT & TESTED

**Requested by the project owner.** Builds on D-019 (platform AI settings) and D-024 (AI Configuration). The originals in
`backend/docs/requirements/` are unchanged.

1. **Four-level cascade.** A camera runs a vision module only when
   `effective(m) = platform_enabled(m) AND NOT org_blocked(m) AND campus_enabled(m) AND camera_enabled(m)`:
   1. *Platform* — `platform_ai_settings` (D-019, unchanged).
   2. *Organization* — **new**: the Super Admin blocks or unblocks one of the four core modules (human detection &
      tracking, Re-ID, weapon, fight / violence) for one organization. Default: nothing blocked. Restricted-area face
      recognition has a block column too, but stays platform-controlled (off by default) and is not on the Super
      Admin's card.
   3. *Campus* — the campus's Admins (D-024, unchanged), plus **new** per-module *Supervisor visibility*.
   4. *Camera* — **explicit opt-in**: a new camera runs **no** module until one is selected.
2. **Organization blocks** (`platform_organization_ai_blocks`, migration 0033). Only the platform role, only through
   `platform_organization_ai_permissions_get` / `_set` (SECURITY DEFINER); tenants have no privilege on the table and
   read their own organization's flags through `app_organization_ai_blocks()` (Admins and Supervisors only). Every block
   / unblock needs the Super Admin's password (SA-SEC-4; a wrong one counts toward the lock and is audited) and is
   written to `platform_audit_logs` as `ORGANIZATION_AI_PERMISSIONS_UPDATED`. **Blocking** switches the module off on
   every camera of the organization at once (the worker's next `ai-config` poll returns it no more); Admins and
   Supervisors see it marked *Blocked for your organization by the platform* and any attempt to switch it on returns
   `403 AI_MODULE_BLOCKED_BY_PLATFORM`. **Unblocking never switches a camera back on**: the Admins choose the cameras.
   The rows cascade from `organizations` (organization erasure).
3. **Supervisor visibility** (`campus_ai_settings.supervisor_can_manage_*`, default true, written by Admins only, with
   their password, through `PUT /ai-configuration/campus`). A hidden module disappears for the campus's Supervisors
   (AI Configuration page, Add / Edit Camera, bulk dialog, alert rules); switching it on or off as a Supervisor returns
   `403 AI_MODULE_HIDDEN_FROM_SUPERVISOR`. **Hiding never switches a module off on a camera**, and every Supervisor
   change (single camera, bulk, camera details) **keeps the hidden modules exactly as they were** (merge rule).
4. **Explicit per-camera opt-in.** No `camera_ai_settings` row ⇒ nothing runs (`app_camera_ai_config` uses
   `COALESCE(camera, false)`; column defaults false). Cameras that existed before migration 0033 got a row with every
   module selected, so their effective modules did not change. `POST /cameras` and `PUT /cameras/{id}` accept an
   optional `ai_modules` object (omitted on add ⇒ all off; omitted on edit ⇒ unchanged; keys left out keep their
   value), validated before anything is written and saved in the camera's transaction; responses carry `ai_modules`
   (selected) and `ai_effective_modules`, filtered to what the caller may see.
5. **Bulk 1-click assignment.** `PUT /ai-configuration/cameras/bulk {camera_ids (1–200), mode ADD|REMOVE|SET, modules,
   current_password}` — Admins and Supervisors, own campus, **all or nothing** (another campus's camera ⇒ 404 and
   nothing changes), password confirmed, audited once as `CAMERA_AI_BULK_UPDATED` (mode, modules, cameras). The
   Cameras page gained search + location filters (existing), row checkboxes, *Select all visible*, a selection counter
   and *Assign AI Features (Bulk)* (Add / Enable — default, Remove / Disable, Replace).
6. **True per-camera model skipping in `cv_inference`.** `GatedDetector` now asks the camera's configuration **before**
   each model of a `CompositeDetector` runs: the weapon detector runs only with weapon detection; the fight classifier
   (and its clip ring buffer, cleared once when switched off) only with fight / violence; the person detector only with
   human detection **or** Re-ID; the Re-ID embedder, gallery and sink only with Re-ID (unchanged `allowed` check).
   With Re-ID on and human detection off, persons are detected **internally only** for the Re-ID crops: kept for
   tracking, never drawn on the Live overlay. Detections of modules that are off are still dropped afterwards (a single
   model may serve several modules). Spy tests assert `call_count == 0` for every skipped model.
7. **Unchanged boundaries:** PostgreSQL RLS (campus scope), the `PLATFORM_DB_USER` isolation (no tenant table; the new
   table reachable only through its two functions), SA-SEC-4 password confirmations, tenant password confirmations and
   audit logging. Existing API keys stay (`human_detection`, `person_reid`, `weapon_detection`, `fight_detection`,
   `face_recognition`); the Super Admin endpoint also accepts the long feature names.
8. **Verification:** see `implementation_log.md` (D-028).
