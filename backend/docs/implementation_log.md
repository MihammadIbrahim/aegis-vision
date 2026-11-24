# Implementation log

One entry per step: what changed, how it was tested, and what remains.
Status words: **done** = implemented and tested; **not started** = no code yet.

---

## 2026-10-27 — Baseline before Phase 1

- `core_api` test suite: **50 passed** (tenancy + RLS slice from migration `0001`).
- Docker is not usable by the developer account yet (`permission denied` on the Docker socket),
  so database tests run against a local PostgreSQL 16 started outside the project.

## 2026-10-27 — Phase 1, step 1a: Platform Super Admin login — done

**What it does.** A Super Admin logs in with username + password (no OTP, by requirement),
gets a 30-minute access token for the `platform` audience, and can read their own profile.
Five failed logins within 15 minutes lock the account for 15 minutes. The first Super Admin
is created, and its password recovered, only by a command that reads `.env`.

**Separation from organization users (tested).**
- Separate table (`platform_admins`) and separate database role (`cctv_platform`).
  The tenant role cannot read `platform_admins`; the platform role cannot read any tenant table.
- Separate token audience: `platform` tokens are refused by organization routes and
  `tenant` tokens by platform routes.

**Database.** New migration `0002_platform_admins` (upgrade and downgrade tested).
Note: the plan listed `0002` as the RLS-hardening migration; that hardening now moves to a
later migration in Phase 1, and the plan's migration table will be renumbered to match.

**Files.**
- Created: `migrations/versions/0002_platform_admins.py`, `src/domain/entities/platform_admin.py`,
  `src/domain/repositories/i_platform_admin_repo.py`, `src/infrastructure/database/models/platform_admin.py`,
  `src/infrastructure/database/repositories/platform_admin_repository.py`, `src/entrypoints/__init__.py`,
  `src/entrypoints/seed_super_admin.py`, tests `unit/test_password_hasher.py`, `unit/test_token_audiences.py`,
  `unit/test_platform_admin_login.py`, `unit/test_settings.py`, `integration/test_platform_auth.py`,
  `integration/test_seed_super_admin.py`, this log.
- Filled (were empty placeholders): `src/infrastructure/security/password.py`,
  `src/application/interfaces/i_password_hasher.py`, `src/application/use_cases/platform_admin_use_cases.py`,
  `src/application/dtos/platform_admin_dto.py`, `src/presentation/api/v1/platform_admin_router.py`.
- Modified: `src/config/settings.py` (reads `backend/.env`, new `PLATFORM_DATABASE_URL`, clear startup errors,
  32-character minimum for `JWT_SECRET_KEY`), `src/infrastructure/security/jwt.py` (token audiences),
  `src/presentation/dependencies.py`, `src/presentation/error_handlers.py` (429 for locked accounts),
  `src/presentation/api/v1/api_router.py`, `src/main.py`, `src/domain/exceptions/domain_errors.py`,
  `src/infrastructure/database/models/__init__.py`, `tests/conftest.py` (platform database settings and
  new fixtures; existing fixtures behave as before), `backend/.env.example`, `backend/.gitignore`.
- Renamed and extended: `docker/postgres/init/02-create-app-role.sh` → `02-create-roles.sh`
  (also creates the platform role).

**Tests.** 83 passed (50 existing + 33 new); ruff clean; migration upgrade/downgrade round trip passed;
manual check with uvicorn + curl passed. Breaking the token-audience check makes the audience
unit tests fail, as intended.

**Remaining for step 1.** Super Admin organization management (a later step), platform audit log
of logins (done in step 1b below), "remember me" / refresh tokens and session list, IP-based rate
limiting (needs Redis).

## 2026-10-27 — Phase 1, step 1b: Super Admin credentials and emergency recovery — done

**What it does.**
- Normal credentials live in the database (Argon2id). New endpoints `POST /api/v1/platform/me/username`
  and `POST /api/v1/platform/me/password` change them after checking the current database password;
  password confirmation, a new-password policy and username uniqueness are enforced.
- Emergency recovery credentials come only from `SUPER_ADMIN_RECOVERY_USERNAME/PASSWORD` in the server
  environment. They log in to the primary Super Admin through the same login endpoint, with normal
  Super Admin permissions, and keep working after the normal credentials change.
- One shared lock for both methods (5 failures in 15 minutes). Generic 401 errors.
- Changing credentials signs out all earlier tokens (token version); the response carries a new token.
- New insert-only `platform_audit_logs`: logins (with `login_method` NORMAL or RECOVERY), failures,
  credential changes. No typed usernames, passwords or tokens are recorded.
- 422 responses no longer echo submitted values, so passwords never come back in an error.
- The seed command now works on the primary account: creates it once, never overwrites it, accepts
  the development password `admin@123` (8+ characters) with a warning; `--reset-password` restores the
  initial values and signs out all sessions.

**Database.** New migration `0003_platform_credentials_and_audit` (upgrade/downgrade tested). The platform
role still cannot update usernames or password hashes directly (the existing security test is unchanged);
credentials change only through `platform_set_admin_credentials()`, which always increases the token
version and refuses stale concurrent changes.

**Files.**
- Created: `migrations/versions/0003_platform_credentials_and_audit.py`, `src/domain/entities/platform_audit_event.py`,
  `src/domain/value_objects/recovery_credentials.py`, `src/domain/services/credential_policy.py`,
  `docs/super_admin_authentication.md`, tests `unit/test_recovery_credentials.py`, `unit/test_credential_policy.py`,
  `unit/test_platform_recovery_login.py`, `integration/test_platform_credentials.py`.
- Modified: `src/domain/entities/platform_admin.py`, `src/domain/exceptions/domain_errors.py`,
  `src/domain/repositories/i_platform_admin_repo.py`, `src/application/use_cases/platform_admin_use_cases.py`,
  `src/application/dtos/platform_admin_dto.py`, `src/infrastructure/database/models/platform_admin.py`,
  `src/infrastructure/database/models/__init__.py`, `src/infrastructure/database/repositories/platform_admin_repository.py`,
  `src/infrastructure/security/jwt.py`, `src/config/settings.py`, `src/presentation/dependencies.py`,
  `src/presentation/error_handlers.py`, `src/presentation/api/v1/platform_admin_router.py`,
  `src/entrypoints/seed_super_admin.py`, `tests/conftest.py` (helpers and fixtures added; existing ones unchanged
  in behavior), `tests/unit/test_settings.py` (tests appended), `backend/.env.example`.

**Tests.** 117 passed (83 existing, unchanged + 34 new); ruff clean; migration round trip passed; manual
check with uvicorn + curl passed for both methods. Removing the token-version check or weakening the
recovery comparison makes tests fail, as intended.

**Known limit.** A Super Admin who forgot the normal password can log in with recovery credentials but
cannot set a new normal password through the API, because the change requires the current database
password. `seed_super_admin --reset-password` on the server covers this today.

## 2026-10-27 — Phase 1, step 1c: Super Admin emergency credential recovery — done

**What it does.** New endpoint `POST /api/v1/platform/recovery/credentials`. In a session opened with
the emergency recovery credentials, and after entering those credentials again, the Super Admin sets a
new normal username and password without the forgotten database password. Both change in one step
(one token-version step), all earlier Super Admin tokens stop working, and a fresh emergency-session
token is returned. The emergency credentials never change.

**Rules enforced.** Recovery session required (normal sessions and pre-existing tokens without a login
method get 403); primary account only; recovery must be configured; username format and uniqueness;
password policy; new password must differ from the emergency password; wrong re-entry returns 403 and
counts toward the shared lock; stale or concurrent changes are refused by the existing
`platform_set_admin_credentials()` database function. Audited as `CREDENTIALS_RECOVERED` without secrets.

**Tokens.** Super Admin tokens now carry the login method (`lm`: `NORMAL` or `RECOVERY`). Tokens without
it count as `NORMAL`. The username/password change endpoints keep the session's method in the fresh token.

**Database.** No migration needed (head stays `0003`); the existing credential function and grants are reused.

**Files.**
- Created: tests `integration/test_platform_recovery_workflow.py`, `unit/test_platform_credential_recovery.py`.
- Modified: `src/infrastructure/security/jwt.py`, `src/domain/entities/platform_admin.py`,
  `src/application/use_cases/platform_admin_use_cases.py`, `src/application/dtos/platform_admin_dto.py`,
  `src/presentation/dependencies.py`, `src/presentation/api/v1/platform_admin_router.py`,
  `tests/conftest.py` (`platform_auth_header` gained an optional `login_method`, default unchanged),
  `docs/super_admin_authentication.md`.

**Tests.** 142 passed, 0 failed (117 existing, unchanged + 25 new); without a database 62 passed and
80 skipped. Ruff clean. Manual uvicorn + curl run passed. Removing the session-method check or the
re-entry check makes tests fail, as intended.

## 2026-10-27 — Organization Admin auth, Stage 2: first Admin registration and email OTP — done

**What it does.** Three public endpoints under `/api/v1/auth/organization-registrations`:
- submit the form (Organization Name, Admin Full Name, Username, Email, Phone, Password);
- resend the code;
- verify the code.

Until the code is verified, only a pending registration row exists. Verification creates, in one
transaction:
- the organization, with its permanent Organization ID (`ORG-` + 11 digits, reserved at submission and
  kept through every retry);
- its "Main Campus";
- the Main Admin, with a user ID of 6 capital letters + 11 digits and an Argon2id password hash.

No token is issued (login is Stage 3). Details: `docs/organization_registration.md`. Decisions:
`docs/requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md` and D-002 to D-005.

**Rules enforced (tested).**
- **Names:**
  - organization names unique ignoring capitalization, and stored exactly as entered;
  - usernames unique ignoring capitalization;
  - duplicate email refused only after OTP (D-005).
- **Codes:**
  - 6 digits, stored only as keyed hashes;
  - 5-minute expiry, 5 attempts, single use;
  - a new code retires the old one;
  - 60-second resend cooldown;
  - 5 codes per email address per hour, across registrations;
  - per-IP request limit.
- **Retention:** unverified registrations expire after 60 minutes. Applicant details are erased on
  completion or expiry; the Organization ID stays reserved.
- **Email:**
  - `EMAIL_DELIVERY_MODE` selects none (503 `EMAIL_DELIVERY_NOT_CONFIGURED`), `dev_outbox`
    (development only) or `brevo` (HTTP API through httpx);
  - a refused send stores nothing (502).
- **Database access:** the app role reaches registration data only through four narrow
  `SECURITY DEFINER` functions (D-002).

**Database.** New migration `0004_organization_registration`. `0001`–`0003` are unchanged.
- **New columns:** `organizations.organization_code`, `official_email` and `official_phone`;
  `users.username` and `phone`.
- **New indexes:** unique `lower(name)` on organizations and unique `lower(username)` on users.
- **ID rule:** the display-ID check also accepts the new format for Admin and Supervisor.
- **New tables:** `user_credentials`, `organization_registrations` and `email_otp_challenges`, plus the
  four functions and two internal helpers.
- **Round trip:** upgrade, full downgrade to base and upgrade again all passed on a fresh database. A
  case-insensitive name clash in existing data stops the upgrade with a clear message.

**Files.**
- **Created:**
  - `migrations/versions/0004_organization_registration.py`;
  - `src/domain/value_objects/organization_code.py`, `src/domain/services/registration_policy.py`,
    `src/domain/repositories/i_organization_registration_repo.py`;
  - `src/application/interfaces/i_otp_codec.py`,
    `src/application/use_cases/organization_registration_use_cases.py`,
    `src/application/dtos/organization_registration_dto.py`;
  - `src/infrastructure/email/dev_outbox.py`, `src/infrastructure/email/factory.py`,
    `src/infrastructure/database/repositories/organization_registration_repository.py`;
  - `src/presentation/api/v1/organization_registration_router.py`;
  - `tests/fakes.py`, `tests/integration/test_organization_registration.py`;
  - unit tests `test_registration_policy.py`, `test_registration_ids.py`, `test_otp_codec.py`,
    `test_email_senders.py`, `test_rate_limiter.py`, `test_registration_settings.py`,
    `test_organization_registration_use_cases.py` and `test_database_connection.py`;
  - `docs/organization_registration.md`, and `docs/requirements/` (the five original documents copied
    unchanged, a README with checksums, and the approved-decisions document).
- **Filled (were empty placeholders):**
  - `src/domain/entities/organization.py`, `src/domain/repositories/i_unit_of_work.py`;
  - `src/application/interfaces/i_email_service.py`, `src/application/interfaces/i_rate_limiter.py`;
  - `src/infrastructure/security/otp.py`, `src/infrastructure/security/rate_limiter.py`;
  - `src/infrastructure/email/brevo_service.py`, `src/infrastructure/database/unit_of_work.py`.
- **Modified:**
  - `src/domain/exceptions/domain_errors.py`: optional `code` / `retry_after`, and new
    `VerificationFailed`, `RateLimited`, `Gone`, `ServiceUnavailable` and `UpstreamServiceFailed`.
    Existing errors are unchanged.
  - `src/domain/value_objects/system_id.py`: new `generate_staff_id()`; the `ADM-` generator is
    unchanged.
  - `src/config/settings.py`: email, OTP and registration settings, all optional.
  - `src/main.py`: the email sender, OTP codec and IP limiter on `app.state`; `create_app` gained an
    optional `email_sender`.
  - `src/presentation/error_handlers.py`: adds `code` and `Retry-After` only when an error carries them.
  - `src/presentation/api/v1/api_router.py`.
  - `src/infrastructure/database/connection.py`: `hide_parameters=True`, so database errors never log
    emails or hashes.
  - `src/infrastructure/database/models/{organization,user,__init__}.py`.
  - `tests/conftest.py`: additive only. `_client_for` gained an optional `email_sender`; new fixtures
    `outbox`, `registration_client` and `owner_execute`.
  - `backend/.env.example`, `backend/.gitignore` (`.dev_outbox/`).
- **Not touched:** Super Admin use cases, routes and migrations; migration `0001`; and the Stage 3
  placeholders `auth_use_cases.py`, `auth_dto.py`, `auth_router.py` and `i_otp_store.py`.

**Tests.** Run against the scratch PostgreSQL 16, not Docker:
- 284 passed, 0 failed, 0 skipped: the 142 existing tests, unchanged, plus 142 new (47 integration,
  95 unit).
- Without a database: 157 passed and 127 skipped.
- Ruff clean.
- mypy: only the earlier error in `branch_repository.py:63`.

Ten deliberate breaks were each caught by at least one test:
- not counting wrong codes, or not committing them;
- no cooldown;
- no hourly limit;
- a case-sensitive name check;
- committing a failed completion;
- not retiring old codes;
- trimming the organization name;
- accepting expired codes;
- completing without a verified email.

A manual uvicorn + curl run in `dev_outbox` mode passed. Neither the code nor the password appeared in
the server log.

**Remaining.**
- **Stage 3:** login with Organization Name + Organization ID + email + password + login OTP.
- **Stage 4:** "Already Registered Organization?" applications, Main Admin approval with password +
  fresh OTP, rejection and 72-hour cleanup, `ADMIN_APPROVAL_PENDING` and `ADMIN_REQUEST_REJECTED`.
- **Not built yet:**
  - audit-log entries for registrations;
  - editing organization contacts;
  - a way to recover a forgotten Organization ID;
  - per-IP limits shared across API replicas.

## 2026-10-27 — Stage 2 amendment: Organization ID format — done

**What changed.** New Organization IDs are 6 capital letters + 11 digits (17 characters, no prefix),
for example `ABCDEF12345678901`. They replace `ORG-` + 11 digits.

**Existing data.** Nothing is rewritten. IDs already issued in the `ORG-` format stay valid, and a
registration that reserved an `ORG-` ID before the change keeps it when it completes (tested).

**Database.** New migration `0005_organization_id_letters_digits`; `0001`–`0004` are unchanged.
- **Format checks:** on `organizations` and `organization_registrations`, they now accept the new
  format or the earlier one.
- **Default:** the database default `app_new_organization_code()` now generates the new format. It
  is still owner-only.
- **Uniqueness:** the unique indexes are unchanged.
- **Downgrade:** restores the old rule as `NOT VALID` if new-format IDs exist, so no row is lost.
- **Round trip:** checked with existing `ORG-` data; upgrade, downgrade and re-upgrade passed.

**Files.** Created `migrations/versions/0005_organization_id_letters_digits.py`. Modified:
- `src/domain/value_objects/organization_code.py`;
- the field description in `src/application/dtos/organization_registration_dto.py`;
- a comment in `src/infrastructure/database/models/organization.py`;
- the Stage 2 tests `tests/unit/test_registration_ids.py`,
  `tests/unit/test_organization_registration_use_cases.py` (example IDs only) and
  `tests/integration/test_organization_registration.py`;
- this log, `docs/organization_registration.md`, and an amendment note on decision B.

**Tests.** 293 passed, 0 failed, 0 skipped on the scratch PostgreSQL 16 (284 earlier + 9 new). Ruff clean.
mypy: only the earlier error in `branch_repository.py:63`.

## 2026-10-27 — Organization Admin auth, Stage 3: sign-in, login OTP and dashboard — done

**What it does.**
- Sign-in with exact Organization Name + Organization ID + email + password, then an emailed code, then
  an organization access token.
- `GET /api/v1/organization-admin/dashboard` for active Admins of active organizations.

Details: `docs/organization_admin_login.md`, D-006.

**Database.** New migration `0006_organization_admin_login`:
- `organizations.status`;
- failed-login fields on `user_credentials`;
- `admin_login_sessions` and `admin_login_otp_challenges`;
- five narrow functions.

The app role has no table rights on any of them. Upgrade, downgrade and re-upgrade passed.

**Files.**
- **Created:**
  - `migrations/versions/0006_organization_admin_login.py`;
  - `src/domain/repositories/i_admin_login_repo.py`;
  - `src/infrastructure/database/repositories/admin_login_repository.py` and
    `organization_admin_repository.py`;
  - `src/application/use_cases/organization_admin_dashboard_use_cases.py`;
  - `src/presentation/api/v1/organization_admin_router.py`;
  - tests `integration/test_organization_admin_login.py` and
    `unit/test_organization_admin_login_use_cases.py`;
  - `docs/organization_admin_login.md`.
- **Filled (were empty placeholders):** `src/application/use_cases/auth_use_cases.py`,
  `src/application/dtos/auth_dto.py`, `src/presentation/api/v1/auth_router.py`,
  `src/domain/repositories/i_organization_repo.py`.
- **Modified (all additive):**
  - `src/application/interfaces/i_otp_codec.py` and `src/infrastructure/security/otp.py`: new
    `hash_login_code`;
  - `src/presentation/dependencies.py`: new `get_current_organization_admin`; existing dependencies
    unchanged;
  - `src/presentation/api/v1/api_router.py`, `src/config/settings.py` (`ORG_LOGIN_*`) and `src/main.py`;
  - models `organization.py`, `user.py` and `__init__.py`;
  - `backend/.env.example`, `docs/decision_log.md`.
- **Not touched:** Super Admin code, migrations `0001`–`0005`, Stage 2 use cases, existing tests.

**Tests.** On the scratch PostgreSQL 16:
- 346 passed, 0 failed, 0 skipped: the 293 earlier tests, unchanged, plus 53 new (36 integration, 17 unit).
- Without a database: 175 passed, 171 skipped.
- Ruff clean; mypy shows only the earlier `branch_repository.py:63` error.

Nine deliberate breaks were each caught by the new tests:
- a case-insensitive name match;
- no Organization ID check;
- ignoring inactive accounts;
- not consuming the code;
- not keeping failure counts;
- ignoring locks;
- no organization-status check;
- letting non-Admins see the dashboard;
- no re-check at verify.

A manual uvicorn + curl run in `dev_outbox` mode passed, with no code or password in the server log.

## 2026-10-27 — Supervisor creation, Supervisor sign-in, password reset — done

**What it does.** See `docs/supervisors_and_password_reset.md` and decisions M–P. Migration `0007`
(round trip passed).

**Tests.** On the scratch PostgreSQL 16, 374 passed:
- the 346 earlier tests, plus 28 new (22 integration, 6 unit);
- one earlier test changed, because Supervisors may now sign in.

Ruff is clean; mypy shows only the earlier `branch_repository.py:63` error. Five deliberate breaks were
each caught:
- confirmation not bound to the caller;
- unverified confirmation accepted;
- form not bound to its creator;
- no session cut-off after a reset;
- emailing non-matching reset requests.

## 2026-10-27 — Supervisor profile, permanent Supervisor IDs, reset with Supervisor ID — done

**Database.** Migration `0008`:
- a trigger making Supervisor IDs permanent;
- `supervisor_profile_changes`;
- two code purposes;
- seven narrow functions.

Round trip passed. Supervisor creation was already as required and is unchanged.

**Tests.** On the scratch PostgreSQL 16, 400 passed: the 374 earlier tests, unchanged, plus 26 new. Ruff
is clean; mypy shows only the earlier error. Five deliberate breaks were each caught:
- applying without a code;
- a new email without its code;
- no ID trigger;
- a change not bound to its Supervisor;
- cancel ignored.

## 2026-10-27 — Guards: creation, sign-in, forgot password, profile — done

**What it does.** See `docs/guards.md` and decisions T–X.

**Database.** Migration `0009`:
- three Guard-only tables;
- functions derived from the 0007 and 0008 patterns;
- the shared sign-in steps widened to Guards.

Round trip passed. The creation use cases are now shared with a `kind` parameter; Supervisor behaviour is
unchanged.

**Tests.** On the scratch PostgreSQL 16, 419 passed:
- the 400 earlier tests, plus 19 new;
- one earlier ID-rule case changed, because Guards now use 6 + 21.

Ruff is clean; mypy shows only the earlier error. Five deliberate breaks were each caught; two tests were
sharpened so the role checks are tested on their own.

## 2026-10-27 — Main Admin ownership transfer and Admin account deletion — done

**What it does.** See decision D-007. Routes, all under `/api/v1/organization-admin`:
- `GET /account/deletion` shows status, blockers and transfer candidates;
- `POST /account/ownership-transfer` (recipient Admin ID + password), then `/resend-otp`, `/verify`;
  `DELETE` cancels;
- `GET /ownership-transfers/incoming`, `POST /ownership-transfers/{id}/accept` or `/reject`;
- `POST /account/deletion` (own Admin ID + password), then `/resend-otp`, `/verify` (deletes).

**Database.** Migration `0010`: two tables (`ownership_transfers`, `admin_account_deletions`), two new OTP
purposes, and SECURITY DEFINER functions; the app role has no direct table access. Round trip passed.

**Tests.** On the scratch PostgreSQL 16, 430 passed: the 419 earlier tests plus 11 new
(`tests/integration/test_ownership_and_deletion.py`). Ruff is clean; mypy shows only the earlier error.
Not checked: Docker, the owner's machine, real email sending.

## 2026-10-28 — Complete campus erasure, optional export, deleted organization names — done

**What it does.** See decision D-008. Migration `0011` replaces the 0010 deletion function.

**Deletion: last active Admin of a campus.** Erased: the campus and everything of it:
- its users, credentials, sign-ins, grants, resets and profile changes;
- one-time codes and pending creations;
- cameras;
- the campus request that created it;
- its files.

**Deletion: last campus.** Also erased: the organization and its registration records, campus
requests, ownership transfers and audit entries. Its name goes to `deleted_organization_names`.

**Deletion: shared campus.** Only the Admin's own account is erased.

**Ownership rules** are unchanged from 0010.

**API changes for the frontend:**
- **Renamed fields:** `organization_will_be_deactivated` → `organization_will_be_deleted` (status) and
  `organization_deactivated` → `organization_deleted` (result).
- **`POST /organization-admin/account/deletion`** now also returns
  `export: {optional: true, download_url, not_included: [...]}`.
- **`GET /organization-admin/account/deletion/{id}/export`** (new) is optional and can be repeated
  while the request is open. It returns a JSON attachment with `Cache-Control: no-store` and contains
  only the caller's own campus; credentials, hashes, codes and internal keys are never included.
  Errors: 404 (not the caller's or no longer open), 410 (expired), 409 (ownership rules).
- **`POST …/deletion/{id}/verify`** errors when it fails:
  - 500 `DELETION_FAILED`: nothing changed.
  - 500 `MEDIA_CLEANUP_INCOMPLETE`: records erased, but some files were not. They are out of reach and
    erased on the next clean-up.
  - It never returns a 200 success in these cases.
- **`GET /platform/deleted-organizations`** (new, Super Admin token only) returns
  `{organization_names: [...]}`.

**Settings.** `CCTV_STORAGE_ROOT` (already in `.env.example`) is now read by the API.

**Tests.** On the scratch PostgreSQL 16, 439 passed: the 430 earlier tests plus 9 new. The earlier
deletion tests were updated for the new behaviour (organization deleted, not inactive); none removed. Ruff is clean; mypy shows only the
earlier error. Three deliberate breaks were each caught. Not checked: Docker, the owner's machine, real
email sending.

**Note.** During this work a lint run without `--line-length 120` re-wrapped imports in 31 unrelated
files. They were restored and checked against their compiled copies (same size and line layout);
no unrelated file is changed.

## 2026-10-28 — Ownership history snapshot — done

**What it does.** See D-009. Migration `0012`:
- adds `from_full_name`, `from_username` and `from_email` to `ownership_transfers`;
- adds the snapshot trigger;
- adds `app_ownership_history()`, granted to the app role only;
- updates `app_admin_deletion_complete` (keep rule, no audit entry on organization deletion).

The upgrade/downgrade round trip passed.

**API.** `GET /api/v1/organization-admin/ownership-transfers/history`, Main Admin only (others get
`403 NOT_MAIN_ADMIN`). It returns a list of:
- `transfer_id`, `previous_owner_name`, `previous_owner_username`, `previous_owner_email`;
- `status` (always `ACCEPTED`);
- `summary`;
- `offered_at`, `accepted_at`.

**Tests.** On the scratch PostgreSQL 16, 446 passed: the 439 earlier tests plus 7 new. Two earlier
assertions changed with the requirements:
- the previous owner's email now remains in exactly one place, the snapshot;
- no audit entry is written when an organization is deleted.

Ruff is clean; mypy shows only the earlier error. Three deliberate breaks were each caught. No
training-data module exists; a stand-in folder test only shows deletion leaves files outside the
campus folders alone. Not checked: Docker, the owner's machine, real email sending.

## 2026-10-28 — Building, Floor & Area Management — done (backend)

**What it does.** See D-010 (permissions) and D-011 (rules).

**Database: migration `0013`.**
- **New tables:** `buildings`, `floors`, `areas` and `guard_location_assignments`.
- **Cameras:** `cameras` gains optional `building_id`, `floor_id` and `area_id`.
- **Isolation:** composite foreign keys keep every link inside one campus.
- **Database rules:**
  - floors only under indoor buildings;
  - an area's floor must be in the same building;
  - at most one link per camera, exactly one per Guard assignment.
- **Row-Level Security:** only Admins write buildings; Admins and Supervisors handle the rest; Guards
  read only through `app_guard_location()`.
- **Replaced functions:** the campus-deletion function (it now removes the locations) and the export
  (it now includes them). The upgrade/downgrade round trip passed.

**API.** `/api/v1/locations` provides:
- the tree;
- buildings, floors and areas (create, edit, delete);
- cameras and Guards linked to each location;
- Guard and camera locations.

`GET /api/v1/guard/location` returns the Guard's own location. Full list: the docstring of
`location_router.py`.

**Tests.** On the scratch PostgreSQL 16, 461 passed, 0 failed, 0 skipped: the 446 earlier tests plus 15
new (`tests/integration/test_locations.py`). One earlier test (single-campus deletion) was made
independent of test order: the shared audit table now has a count taken before the deletion to
compare against. Ruff is clean; mypy shows only the earlier error. Four deliberate breaks were each
caught. Not checked: Docker, the owner's machine, real email sending.

**Not built:**
- camera add/edit/remove and AI settings;
- alert routing by location;
- the location audit trail;
- all screens.

## 2026-10-28 — Camera Management — done (backend)

**What it does.** See D-012.

**Database: migration `0014`.**
- **New columns on `cameras`:** `connection_type`, `stream_url`, `updated_at`.
- **New rules:**
  - checks on the display name and stream URL;
  - no credentials in the URL;
  - the URL must match the connection type;
  - unique name and URL per campus.
- **Row-Level Security:** writes split out, so only Admins and Supervisors can write.
- **Export:** camera entries now include the connection type and URL.

The upgrade/downgrade round trip passed.

**API** (`/api/v1/cameras`):
- **`GET`:** the list, filterable with `?q=`, `?building_id=`, `?floor_id=` and `?area_id=`;
- **`GET /{id}`:** details;
- **`POST`:** add a camera;
- **`PUT /{id}`:** replace its details and location;
- **`DELETE /{id}`:** remove it.

Responses add `connection_type`, `stream_url`, `environment` (INDOOR/OUTDOOR), the location path and
timestamps. The earlier `id`, `campus_id` and `display_name` are unchanged.

**Tests.** On the scratch PostgreSQL 16, 494 passed, 0 failed, 0 skipped: the 461 earlier tests, 31 new
integration tests (`tests/integration/test_cameras.py`) and 2 new unit tests. No earlier test was
changed. Ruff is clean; mypy shows only the earlier error. Four deliberate breaks were each caught.
Not checked: Docker, the owner's machine, real cameras.

**Not built:**
- online/offline status and the connection test;
- streaming;
- per-camera AI settings;
- camera credentials;
- the camera audit trail;
- all screens.

## 2026-10-29 — Documentation brought up to date (no code changed)

**Changed:**
- the proposal's status sections;
- the architecture's Parts A/B (migrations 0010–0014, 85 operations, test counts);
- the status tables of the four page specifications;
- two outdated "Still open" rows in the clarifications.

**Written** (they were empty): `api_contracts.md`, `event_contracts.md`, `architecture.md`,
`role_permission_matrix.md`, `backend/README.md`, and the four ml_training/weights READMEs.

**Tests.** The scratch test database no longer existed (its temporary folder was cleared), and
reinstalling it was out of scope. Only the 184 tests that need no database ran; all passed. The last
full run is still the 494 of 2026-10-28.

## 2026-10-31 — Alert Management — done (backend)

**What it does.** See D-013.

**Database: migration `0015`.**
- **New tables:** alerts, alert_recipients, alert_escalations, alert_events, alert_messages,
  manual_reports, user_notifications, organization_audit_logs.
- **New functions:** app_alert_ingest, app_alert_guard_action, app_manual_report_submit,
  app_guard_campus_locations.
- **Database rules:** a status-change trigger, and high priority enforced for weapon and
  fight/violence.
- **Row-Level Security:** staff see their campus; Guards see only alerts sent to them and their own
  reports. Audit rows are insert-only.
- **Also:** campus deletion and the export are extended.

The upgrade/downgrade round trip passed.

**API** (23 new operations, 108 in total; see `api_contracts.md`):
- `/internal/detections`;
- `/alerts…`: list, detail, history, acknowledge, responding, assessment, resolve, dismiss,
  escalate, messages;
- `/guard/sync`, `/guard/manual-reports`, `/guard/locations`;
- `/manual-reports…`;
- `/notifications…`.

Settings `DETECTION_INGEST_TOKEN`, `ALERT_DEDUP_SECONDS` and `MANUAL_REPORT_PHOTO_MAX_BYTES` are in
`.env.example`.

**Tests.** On a rebuilt scratch PostgreSQL 16 (the old one had been wiped), 520 passed, 0 failed, 0
skipped:
- the 494 earlier tests (one export assertion updated, because alerts are now exported);
- 26 new tests in `tests/integration/test_alerts.py`.

Ruff is clean; mypy shows only the earlier error. Four deliberate breaks were each caught: floor
routing, non-recipient Guard actions, offline replay, and cross-campus escalation.

**Not built or not verified:**
- real AI detections;
- push/WebSocket delivery;
- automatic assistance broadcast;
- an unacknowledged-alert timeout;
- incidents;
- evidence and clips;
- audit-log viewing;
- all screens.

## 2026-10-31 — CV processing foundation — done (no real model)

**What it does.** See D-014 and `services/cv_inference/README.md`. The pipeline runs recorded video →
sampled frames → detector → IoU tracker → event rules → HTTP sink → Core API `/api/v1/internal/detections`.

**Real and tested:**
- video input and sampling;
- rules and stable IDs;
- the sink: retries, error handling, no token in logs;
- the ONNX Runtime adapter (on a generated one-node graph);
- settings and the command line.

**Test-only:** `SyntheticScriptedDetector`.

**Interfaces only:** `ClipDetector`, `Embedder`.

**Backend.** Migration 0016 (`alert_detection_ids`) makes every detection ID idempotent. The
upgrade/downgrade round trip passed.

**Tests:**
- **cv_inference:** 33 passed (video, detector/tracker/ONNX, rules, sink, pipeline). No database,
  weights or GPU needed.
- **core_api:** 523 passed: 520 earlier, plus 3 end-to-end tests (`test_cv_pipeline_e2e.py`) that run
  the pipeline as a separate process against Core API served by uvicorn. One regression check was
  added to `test_alerts.py`.

**Quality checks:**
- **Deliberate breaks:** four in the CV code (confirmation, stable IDs, 401 handling, sampling) and
  one in the 0016 fix. Each was caught.
- **Lint and types:** ruff clean; mypy shows no issues in cv_inference and only the earlier error in
  core_api.

**Not built:**
- a real detector or weights;
- RTSP input;
- clip buffering;
- Re-ID;
- evidence snapshots;
- a durable retry queue;
- GPU measurements.

## 2026-10-31 — Real person detector and ByteTrack-style tracking — done (cv_inference)

**What it does.** See D-015 and `services/cv_inference/README.md`. Video → sampling → YOLOX-Tiny
(`detectors/human_detector.py`) → `ByteTracker` → rules (persons never alert) → sink.

**New pieces:**
- `benchmark.py`;
- `TrackObserver` hook; `Track.camera_id` and `confirmed`;
- `OnnxRuntimeAdapter` input/output details.

**Weights:** `weights/yolox_tiny.onnx`, downloaded with approval, SHA256 recorded, not in git.
`.gitignore` gained an exception for test fixture graphs only.

**Measured** on an i5-5200U CPU: inference 74.6 ms mean (p95 88.7); full detect 76.8 ms mean
(p95 90.8); about 13 fps for the detector alone. Accuracy on CCTV is **unmeasured** (no labelled
footage).

**Tests:**
- **cv_inference:** 57 passed, 0 skipped. That's 24 new: mocked-output, fixture, real-model and
  real-pipeline tests.
- **core_api:** 524 passed, 0 failed. One new end-to-end test runs the real model against the live
  API: one track, 0 alerts.

**Quality checks:**
- **Deliberate breaks:** five (grid offset, objectness, rescaling, low-score stage, motion
  prediction). Each was caught.
- **Lint and types:** ruff clean; mypy clean in cv_inference.

**Not built:** weapon, fight, face, unauthorized-area, cross-camera Re-ID.

## 2026-11-01 — Organization Personnel Management, Excel/CSV import, restricted-area access — done

**What it does.** See D-016 and `personnel_management.md`.
- **Personnel:** campus-isolated records with a system Personnel ID, configurable categories and
  controlled custom fields, Admin-only registration and changes, read-only basic access for
  Supervisors, history and photos.
- **Import:** Excel/CSV with a campus template, flexible column mapping, streaming validation, an
  annotated error workbook, all-or-nothing confirmation, history and rollback by batch.
- **Restricted-area access:** a person ↔ restricted area relationship with validity, weekly
  schedules, revocation, history and an access check.

**Migration 0017** (`personnel_and_restricted_access`):
- **Tables:** `personnel_categories`, `personnel_custom_fields`, `personnel`,
  `personnel_import_batches`, `personnel_import_issues`, `personnel_import_mapping_profiles`,
  `restricted_areas`, `personnel_area_access`.
- **Functions:** `app_personnel_directory`, `app_area_access_list`, `app_personnel_audit_trail`.
- **Triggers:** built-in categories for new campuses; an active grant needs a restricted area.
- **Replaced:** campus deletion and export now include the personnel data.
- **Checked:** upgrade, downgrade and upgrade again, with and without existing campuses.

**Backend pieces:**
- **Domain:** `personnel.py`, `area_access.py`, `personnel_rules.py`, `access_policy.py`.
- **Import engine:** `application/services/personnel_import/` (reader, mapping, validator,
  annotator, template).
- **Use cases, repositories and routers:** for personnel, imports and restricted access.
- **Settings:** `PERSONNEL_IMPORT_MAX_UPLOAD_BYTES`, `PERSONNEL_IMPORT_MAX_UNCOMPRESSED_BYTES`,
  `PERSONNEL_IMPORT_FILE_RETENTION_HOURS`, `PERSONNEL_IMPORT_CHUNK_ROWS`,
  `PERSONNEL_PHOTO_MAX_BYTES`.
- **Small shared changes:**
  - domain errors may carry per-field `errors`;
  - campus storage gained `local_path`, for streaming large files;
  - `SqlActorBinding` re-binds the user after a rollback;
  - Location Management reports areas that have access history.
- **Removed:** the seven empty `authorized_person_*` placeholder files.

**Bug found during testing:** reading a record back after `commit()` lost the Row-Level Security
binding, because the binding is per transaction. Every new use case now reads back before
committing (the pattern the camera module already used).

**Web app (`website/`, new):** React + Vite + TypeScript.
- **Pages:** sign-in (password, then the emailed code); Personnel Directory; person details; add and
  edit form (required and optional markers, category-specific rules, custom fields, location);
  bulk-import wizard; import history; restricted-area access; categories and fields.
- **Roles:** Supervisors get the read-only directory, basic details and current authorizations.
  Guards are sent to the mobile app.

**Tests:**
- **core_api:** 592 passed, 0 failed (524 before; 68 new). New: 39 unit tests (rules, import
  engine, access policy) and 29 database integration tests (personnel, import, restricted access).
- **What the integration tests check:**
  - the role matrix (Admin, Supervisor, Guard, Super Admin) on every route;
  - campus isolation in the API and directly in the database;
  - the import workflow, from a valid file to blocking errors, warnings and revalidation;
  - rollback, limits and discard;
  - a 20,000-row import;
  - campus deletion and export.
- **website:** 11 vitest tests (role-based navigation and pages, API errors, column mapping),
  `tsc` clean, production build OK. A live check through the Vite proxy against the API passed;
  the pages were **not** clicked through in a browser.

**Quality checks:** ruff clean (`E,F,I,B,UP`, line length 120). mypy is clean apart from one earlier
error in `branch_repository.py` (not changed here).

**Not built:**
- face recognition and identity candidates;
- import as a background job (confirmation runs within the request);
- a Supervisor photo view, which waits for the identity-review workflow.

## 2026-11-01 — Web app Phases 0–2: safety net, design tokens and themes, shared UI components — done

Approved plan, Phases 0–2 only (`website/`). No change to backend code, permissions or Personnel
behaviour.

- **Phase 0, safety net:**
  - 18 characterization tests pin the current Personnel pages: form request body, category-based
    required markers, field errors, Admin/Supervisor detail views, import upload / INVALID /
    warning acknowledgement, grant and revoke bodies, Supervisor read-only view, and Admin-only
    routes;
  - a live smoke script, `website/smoke/personnel_smoke.py`, runs through the Vite proxy against
    the real API on a **new scratch database**. Registration and sign-in are real, with
    development-outbox codes. Result: 31/31 before and after Phases 1–2;
  - one test helper changed shape (recorded calls); one existing assertion was updated to match,
    and it checks the same thing.
- **Phase 1, tokens and themes:**
  - `styles/tokens.css` holds the light and dark palettes; `base.css` and `components.css` replace
    `styles.css`, keeping every class name;
  - a contrast test covers 66 pairs × 2 themes, all ≥ AA. Light disabled text was darkened from
    `#5b6b80` to `#4f5d70`, because it measured 4.41:1;
  - theme switching (system default, stored choice, no flash);
  - IBM Plex self-hosted (`@fontsource/ibm-plex-sans`, `@fontsource/ibm-plex-mono`);
  - the status map in `lib/status.ts`.
- **Phase 2, shared UI:**
  - `lucide-react` added;
  - `components/ui`: Button, Field, Dialog, ConfirmDialog, Toast, DataTable, Pagination,
    PageHeader, states, StatusBadge;
  - `hooks/useAsync`, `hooks/useConfirm`, `lib/format`;
  - 16 component tests (keyboard, focus trap and return, Escape, labels, live regions, stale
    results).
- **Results:** vitest 183 passed; `tsc` clean; production build OK.
- **Not yet done:** pages still use their own markup. The shared components are used from Phase 4,
  after Phase 3 (app shell) is approved.

## 2026-11-01 — Web app Phase 4: Personnel moved into features, shared components, dialogs — done

- **Structure:** `features/personnel` (directory, details, form, settings, `PersonnelSelector`),
  `features/personnel-import` (history, wizard split into one file per step),
  `features/restricted-access` (page plus GrantList, GrantForm, AccessCheckForm,
  RestrictedAreasPanel) and `features/locations` (the location-tree API used by the form).
  - Each feature has `api.ts`, `types.ts`, `routes.tsx` and a public `index.ts`.
  - `api/endpoints.ts` and the old `pages/` folder are gone.
  - Sign-in moved to `auth/`, and `AdminOnly` to `auth/AdminOnly.tsx`.
- **Shared components:** PageHeader, DataTable, Pagination, Button, Field, ErrorState and
  LoadingState, StatusBadge and toasts now used across the Personnel pages.
- **Browser prompts replaced:** all four `window.prompt` calls are gone:
  - deactivate (with an optional reason);
  - revoke access (optional reason);
  - change validity (a dialog with a date-time field);
  - roll back an import (optional reason).

  Reactivate, remove photo, discard import and clear a restricted area now ask for confirmation.
  "Saved with warnings" is a toast instead of a 2.5-second delayed redirect.
- **Unchanged:** routes, request URLs, methods and bodies (checked by the characterization tests),
  permissions, and both themes. Supervisors still never request history, photos, revoked
  permissions or unrestricted areas.
- **Tests:** vitest 207 passed (7 new for the changed behaviour; 3 characterization tests updated
  from `window.prompt` to the dialogs, with the same request bodies). `tsc` clean, build OK, live
  smoke check 31/31 on a scratch database.

## 2026-11-01 — Web app Phase 6: Alerts, Manual Reports and Notifications pages — done

Web pages for the existing Alert Management API (decision D-013), for Organization Admins and
Supervisors of their own campus. **No backend change.**

- **Alerts** (`features/alerts`): list with status, priority, type and building/floor/area filters
  (server order: open first, high priority first, newest first); details with camera, location,
  confidence, detections, recipients (progress and assessment), response history and the alert chat;
  Resolve (optional note), Dismiss (reason required) and Escalate (whole campus, a location, or
  chosen Guard IDs) in dialogs, each with a `client_action_id`. Closed alerts show no actions and no
  message box. A notice states that AI detections are signals for review, not proof.
- **Manual Reports** (`features/manual-reports`): list and details, the photo (fetched with the
  token, never cached), and "Mark as reviewed" with an optional note. Labelled as Guard reports, not
  AI alerts. Guards still submit reports only through the API (the mobile app is not built).
- **Notifications** (`features/notifications`): the user's own notifications, unread-only and type
  filters, open the linked alert or report (marks it read), mark one or all as read; a topbar link
  with the unread count, read when the page changes, after reading and once a minute (the API has
  no push).
- **Shared:** `LocationPicker` in `features/locations`; `Pagination` also works without a total
  (the alert and report lists have none); priority, recipient and read states in `lib/status.ts`;
  an "Operations" navigation group for both roles.
- **Found, not changed:** `GET /alerts` always returns `recipients: []` (only `GET /alerts/{id}`
  loads them), so the list shows no Guard count.
- **Tests:** vitest 225 passed (18 new; 3 navigation/route assertions updated for the new group and
  the topbar's notification request). `tsc` clean, build OK. Core API 592 passed (unchanged) on a new
  scratch PostgreSQL 16. Live smoke checks through the dev proxy on a separate scratch database:
  `smoke/operations_smoke.py` 34/34 (real detections, Guard actions, escalation, dismissal,
  resolution, reports with photo, notifications, and a second organization that sees nothing) and
  `smoke/personnel_smoke.py` 31/31.

## 2026-11-01 — Web app Phase 5: Admin and Supervisor dashboards — done

`features/dashboard`, the start page for both roles (`/` and unknown paths now open `/dashboard`).
**No backend change.** Every figure comes from an existing endpoint, scoped by the server to the
caller's own campus; no request carries an organization or campus ID.

- **Sources:** `GET /organization-admin/dashboard` or `GET /supervisor/dashboard` (organization,
  campus, role, staff and camera counts); `GET /alerts?limit=500` (open, high-priority open, not yet
  acknowledged, open by status and type, the first five open alerts); `GET /manual-reports?limit=500`
  (awaiting review, latest five); `GET /notifications?limit=100` (unread, recent activity);
  `GET /locations` (buildings, perimeters, floors, areas, per-building cameras and assigned Guards);
  `GET /restricted-access/areas` (restricted areas and current grant counts); Admins only:
  `GET /personnel?status=ACTIVE&limit=1` (active personnel total).
- **Honest counts:** where a list reached the API's page limit, the figure is shown as "N+" and a
  notice says so. The dashboard endpoints' `not_available_yet` list (still naming "alerts" and
  "guard_reports") is not used.
- **Not shown (no backend source):** camera online/offline, AI detection statistics, incidents,
  trends over time and response-time metrics, storage, live CCTV preview.
- **Found, not changed:** `GET /restricted-access/areas` returns the Admin's area `access_note` to
  Supervisors too; the web pages never display it. Guard counts per building include any assigned
  Guard (removal of Guards is not built, so all are active today).
- **Tests:** vitest 235 passed (10 new; 4 navigation/route assertions updated for the Dashboard
  entry and the new start page). `tsc` clean, build OK. Live on a new scratch database: operations
  smoke 34/34, personnel smoke 31/31, and every dashboard request checked for both roles
  (Supervisor refused the Admin dashboard and the reverse). Screenshots in both themes at 1440, 820
  and 390 px; no horizontal page overflow.

## 2026-11-01 — Security fix: the area `access_note` is not sent to Supervisors — done

The Admin-authored area-level `access_note` is an Admin note (D-016 item 16). Before the fix,
`GET /restricted-access/areas` returned it to Admins **and** Supervisors (one response model for both;
the web pages never displayed it). Now:

- `ViewRestrictedAccess.areas` blanks the note for anyone who is not an Admin;
- the router answers Supervisors with `SupervisorRestrictedAreaResponse`, which has **no**
  `access_note` field; Admins keep `RestrictedAreaResponse` unchanged;
- no change to other routes, permissions, data or the database. The web type marks the field as
  optional; no page reads it.

**Tests:** new integration test `test_the_area_access_note_is_for_admins_only` (Admin receives the
note, with and without `include_unrestricted`; the Supervisor response has no such field or text but
keeps the area details and grant counts; `include_unrestricted` and changing an area stay 403; grant
lists keep basic personnel details; another campus's Supervisor sees only their own area and gets 404
for this one) and `tests/unit/test_area_access_note.py` (3 tests). Core API **596 passed, 0 failed**.
Web: 2 new tests (Supervisor restricted-access page and dashboard without the field); vitest 237
passed, `tsc` clean, build OK. Live on the scratch database: the Admin receives the note, the
Supervisor response has none; personnel smoke 31/31, operations smoke 34/34.

## 2026-11-11 — Web Phase 7: Buildings & Locations and Cameras pages — implemented, awaiting review

Frontend only (`website/`); **no backend change**. Built on the Building, Floor & Area Management and
Camera Management APIs (D-010 to D-012).

- **`features/locations`** (`/locations`, "Buildings & Locations"): collapsible tree of buildings and
  outdoor perimeters → floors → areas, with camera/Guard counts and boundaries; Admins add, edit and remove
  buildings and perimeters; Admins and Supervisors add, edit and remove floors (optional level −20…300) and
  areas (optional floor, boundary direction or custom name, Inside/Outside); "Linked" lists cameras and
  Guards at or below a location; non-empty locations are explained instead of offered for removal (the
  server checks again).
- **`features/cameras`** (`/cameras`, "Cameras"): table (name, Camera ID, connection type, stream URL,
  Indoor/Outdoor, location); search as you type (`q`, 300 ms pause) and building/floor/area filters; add/edit
  dialog (display name, RTSP/ONVIF, optional stream URL, optional location; `location: null` removes it);
  removal after confirmation. The form refuses a stream URL with a user name or password, a scheme not
  matching the connection type, or a bad host/port, mirroring `camera.py`.
- **Shared:** "Site & Infrastructure" sidebar group (both roles); `canManageBuildings` / `canManageSite` in
  `auth/permissions.ts`; `FormDialog` in `components/ui`; status looks for `INDOOR`, `OUTDOOR`,
  `OUTDOOR_PERIMETER`; location tree types now carry every field of the tree response.
- **Endpoints used:** `GET /locations`; `POST /locations/buildings`; `PUT|DELETE /locations/buildings/{id}`;
  `POST /locations/buildings/{id}/floors`; `PUT|DELETE /locations/floors/{id}`;
  `POST /locations/buildings/{id}/areas`; `PUT|DELETE /locations/areas/{id}`;
  `GET /locations/{buildings|floors|areas}/{id}/linked`; `GET /cameras` (`q`, `building_id`, `floor_id`,
  `area_id`); `POST /cameras`; `PUT|DELETE /cameras/{id}`. Not used: `/locations/camera-links…` and the
  Guard-assignment endpoints (left for Phase 8).
- **Not built (no backend support):** live video, online/offline status, connection test, per-camera AI
  settings, change audit.
- **Tests:** `src/test/phase7.test.tsx` (26 new); 2 navigation assertions updated for the new group. vitest
  **263 passed**, `tsc` clean, build OK. Live on a new scratch database through the dev proxy: new
  `website/smoke/site_smoke.py` **40/40**, operations 34/34, personnel 31/31. Responsive check at 1440, 820
  and 390 px, both themes, both roles: one page-level overflow found on the Cameras page at 390 px (a
  visually hidden table header escaping the scroll box) and fixed by making the "Actions" header visible;
  afterwards page width equals the viewport on both pages and dialogs fit on a phone.

## 2026-11-11 — Web Phase 8: Staff Management (backend + web) — implemented, awaiting review

Decision **D-017**. Backend change (migration **0018**) plus the `website/` Staff pages.

- **Requirement change:** only Admins create Guards (Supervisors could before). API: `GuardCreatorDep` is
  Admin-only (`ADMIN_ONLY`). Database: the eight Guard step-up/creation functions are re-created with an
  Admin-only actor rule.
- **New API (`staff_router.py`):** `GET /staff` (`role`, `status`, `category`, `q`, `limit`, `offset`),
  `GET /staff/{id}`, `PUT /staff/guards/{id}`, `PUT /staff/guards/{id}/password`,
  `POST /staff/{id}/deactivate`, `POST /staff/{id}/reactivate`. Changes need the acting user's own
  password; deactivation and reactivation are Admin-only.
- **Migration 0018:** `app_staff_actor_lookup()`, `app_staff_set_guard_password(uuid, text)` (moves
  `password_changed_at`, so the Guard's earlier sessions end), notification kinds `GUARD_UPDATED`,
  `GUARD_PASSWORD_SET`, `GUARD_LOCATION_CHANGED` and the `subject_user_id` target.
- **Audit and notifications:** every change writes `organization_audit_logs` (field names only).
  Supervisors' changes notify the campus's active Admins. `/locations/guard-assignments` (unchanged
  contract) now audits too, through `GuardLocationActivity`.
- **Guard categories:** STANDBY (no location) / ASSIGNED (a location), derived from
  `guard_location_assignments`; deactivation removes the assignment.
- **Web (`features/staff`):** `/staff` list with Guards/Supervisors tabs (Supervisors: Guards only),
  search, Standby/Assigned and status filters; `/staff/:id` with category, post, account details, edit,
  set password, assign location / make Standby, deactivate/reactivate (Admins); the four-step creation
  wizard (Admins). Notifications page and dashboard link staff notifications to the Guard. Nav: "Staff".
- **Found and fixed during the live check:** reading the record back after `commit()` failed, because the
  Row-Level Security actor is transaction-local; the use cases now read back before committing.
- **Tests:** backend `tests/integration/test_staff_management.py` (7) and a changed Guard-creation test
  (Supervisors refused, also directly in the database); core_api **603 passed, 0 failed** (596 before). Web: `src/test/phase8.test.tsx` (16 new) and 2
  navigation assertions updated; vitest **279 passed**, `tsc` clean, build OK. Live on a scratch database:
  new `website/smoke/staff_smoke.py` **25/25**; site 40/40, operations 34/34, personnel 31/31 (the operations
  and site scripts now create Guards as the Admin). Responsive check: Staff pages and the wizard, both roles,
  both themes, 1440/820/390 px, no page-level overflow.

## 2026-11-11 — Guard operational mode and forced sign-out (D-018) — implemented, awaiting review

Backend (migration **0019**) plus label changes in `website/`.
- **Sign-in:** `operational_mode` in `POST /auth/guard/login` and `/login/{id}/verify`; checked against the
  assignment after the right password and again at the code step (`409 OPERATIONAL_MODE_MISMATCH`); the token
  response and `GET /guard/dashboard` add `operational_mode` and `dashboard`.
- **Forced sign-out:** trigger on `guard_location_assignments` sets `user_credentials.sessions_revoked_at`;
  `get_current_actor` refuses older Guard tokens (`401 ASSIGNMENT_CHANGED`); tokens carry `iat_us`.
- **Forgot password:** `operational_mode` required with `guard_id`; mismatch = same answer, no code.
- **Rename:** staff category `ASSIGNED` → `STATIONED` (API, web, tests, smoke); web labels "Stationed /
  Standalone"; the location dialogs say the Guard is signed out.
- **Tests:** new `tests/integration/test_guard_operational_mode.py` (5); Guard tests send the mode; a
  "wrong-mode" case in the forgot-password test. Results: core_api **609 passed, 0 failed** (603 before). Web: 279 passed, `tsc` clean,
  build OK. Smoke: staff 32/32, operations 36/36, site 40/40, personnel 31/31.

## 2026-11-12 — Platform Super Admin management backend (D-019) — implemented, awaiting review

Backend only; `super_admin/` untouched.
- **Migration 0020:** seven platform functions (overview, organization list/detail/campuses/names, Main Admin
  update, status), `platform_ai_settings`, trigger for `ORGANIZATION_REGISTERED`, audit-log indexes.
- **API (12 operations; 166 in total):** `GET /platform/overview`; `GET /platform/organizations` (+ `/export`,
  `/{organization_id}`); `PUT /platform/organizations/{organization_id}/main-admin`;
  `POST /platform/organizations/{organization_id}/status`; `GET`/`PUT /platform/ai-settings`;
  `GET /platform/health`; `GET /platform/audit-logs` (+ `/actions`, `/export`).
- **Behaviour change:** `get_current_actor` refuses tenant requests of non-active organizations on every route.
- **Settings:** `STORAGE_WARNING_PERCENT` (80), `STORAGE_CRITICAL_PERCENT` (90).
- **Tests:** `tests/integration/test_platform_management.py` (12) and `tests/unit/test_platform_health.py` (4):
  boundaries (tenant tokens refused, platform role refused on tenant tables, tenant role refused on platform
  functions), overview, list/search/sort/paging, details, CSV export and formula safety, Main Admin edit with
  password, lockout after five wrong passwords, suspension blocking and restoring access, AI settings, health,
  audit filters/paging/export. Results: core_api **625 passed, 0 failed** (609 before); ruff clean, mypy no new errors.
- **Contract:** the SRS section at the end of `FYP_Final_Super_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt`.

## 2026-11-12 — Platform Super Admin web app (`super_admin/`) — implemented, awaiting review

Frontend only; no backend change.
- **App:** React 19 + Vite + TypeScript, independent of `website/` (own build, copies of the design tokens, styles and
  UI components). Session token in `sessionStorage` under `cctv.platform.accessToken`; theme `cctv.platform.theme`.
- **Pages:** sign-in; platform overview; organizations (search, status, sort, paging, CSV export, deleted names);
  organization details (campus counts, Main Admin edit and status change with the current password); AI settings;
  system health; audit logs (filters, paging, CSV export); profile (username, password, emergency recovery).
- **Contract:** `src/api/types.ts` / `src/api/platform.ts` follow the SRS section field for field;
  `super_admin/smoke/platform_smoke.py` checks every route and every response's fields live (29/29).
- **Tests:** 183 Vitest tests (21 app tests + copied UI and contrast tests); `tsc` clean; build OK. Responsive check:
  60 screenshots, no page-level overflow (fixed: hidden table text escaping the scroll box).
- **Found:** the docs said the Organization ID is `ORG-` + 11 digits; since migration 0005 new IDs are 6 capital letters +
  11 digits. Corrected in the SRS section; the backend DTO field description still says `ORG-` (code not changed in this
  frontend-only task).

## 2026-11-12 — Public landing page and Create Account page (`website/`) — implemented, awaiting review

Frontend only; no backend, database or `super_admin/` change.
- **Routes (signed out):** `/` → landing page; `/register` → Create an organization account; any other path →
  sign-in (as before). Signed in, `/` → `/dashboard` (unchanged).
- **Landing page** (`src/features/landing/`): section navigation, light/dark switch on the shared `cctv.theme` key,
  Sign in (`/login`) and Create account (`/register`), small-screen menu; hero with a labelled sample campus map,
  capabilities, how it works, roles and security, architecture, footer. Every claim has an Available / Partly built /
  Planned badge from `content.ts`; design tokens only (12-column grid, 1200 px container).
- **Create account** (`src/features/registration/`): uses the existing routes
  `POST /api/v1/auth/organization-registrations`, `…/{id}/resend-otp`, `…/{id}/verify`; shows the Organization ID
  and links to sign-in. The sign-in page gained a home link and a "Create an account" link.
- **Tests:** `src/test/landing.test.tsx` (11). Results: web **290 passed** (279 before); `tsc` clean; build OK.
  Responsive check: 22 screenshots (1440/820/390 px, light and dark, menu, registration, sign-in), no page overflow.

## 2026-11-12 — Web UI overhaul with Tailwind CSS (`website/`) — implemented, awaiting review

Frontend styling only; no change to API calls, request bodies, routes, auth state or validation rules. No backend change.
- **Tailwind CSS v4** (`tailwindcss`, `@tailwindcss/vite`), without its preflight reset. `src/styles/index.css` sets
  the cascade layers `theme → base → components → utilities` and registers every colour token as a Tailwind colour
  (`bg-surface`, `text-fg-muted`, `border-border`, …), so both approved themes are kept. New token `--color-canvas`
  (light `#f4f6f9`, dark = page colour) for the signed-in page background; added to the contrast test.
- **Shared classes** (`components.css`) rebuilt with `@apply` under the same names: buttons (new `outline`
  variant and `lg` size, `buttonClass()` for links), inputs with focus rings, cards, tables, badges, tabs, filters
  (search icon), dialogs, dashboard tiles (tinted icon badges, monospace numbers).
- **App shell:** brand mark, grouped navigation with an active-page bar, topbar with section / page title and a
  profile chip; compact topbar on phones.
- **Sign-in and registration:** new top bar (brand, Back to home, theme switch) and a branding panel on large
  screens; icons in fields, show/hide password (`PasswordInput`), two-column registration form, step indicator.
- **Landing page:** rewritten with Tailwind utilities; fixed button alignment, equal-height cards without orphans.
- **Tests:** web **302 passed** (290 before + 12 contrast checks for the new token); `tsc` clean; build OK. Live
  on a scratch database: operations 36/36, site 40/40, staff 32/32, personnel 31/31. Screenshots of 13 pages at
  1440/1280/820/390 px in both themes, no page-level overflow.
- **Not built:** the web app has no Forgot Password page (the API's password-reset routes exist); adding it is a
  separate feature.

## 2026-11-12 — "Already Registered Organization?" campus requests (D-020, migration 0021) — implemented, awaiting review

Requirements Section 8; decisions G, I and L of the approved clarifications.
- **Migration 0021:** `campus_request_submissions` (pending applications, details erased on completion/expiry),
  `campus_request_credentials` (applicant password hash; deleted by trigger once the request is decided);
  `campus_registration_requests` gains `applicant_username`, `applicant_phone`, `email_verified_at`; code purpose
  `CAMPUS_REQUEST_EMAIL`. Public functions `app_campus_request_submit / _issue_otp / _verify_otp / _login_lookup`;
  Main Admin function `app_campus_request_approve`. The app role has no rights on the new tables.
- **API:** `POST /auth/campus-requests`, `…/{id}/resend-otp`, `…/{id}/verify` (public, per-IP limit shared with
  registration). The organization must match its exact name **and** Organization ID. Verified requests appear in
  `GET /campus-requests` (four new response fields). Sign-in answers `403 ADMIN_APPROVAL_PENDING` for a verified,
  undecided applicant with the right password (no session, no code).
- **Behaviour change:** approval now creates a working Admin (username, phone, password from the application) through
  one database function; before, it created an Admin without a password. Verified requests expire 72 hours after
  verification (not listed, cannot be approved, deleted by the next clean-up).
- **Web:** centred sign-in / registration card (side panel removed); `/register/campus` with a "New organization /
  Add a campus" switch; sign-in links to both.
- **Tests:** backend `tests/integration/test_campus_request_applications.py` (8), approval and sign-in unit tests
  updated/added; one contract test now expects the four new response fields. Results: core_api **635 passed, 0 failed**
  (625 before); ruff (I, F, E) clean on the changed files; mypy shows no error in new code. Web: `src/test/campusRequest.test.tsx` (7);
  web 309 passed, `tsc` clean, build OK. Live through the dev proxy: 9/9 (wrong ID refused, submit, verify, pending
  sign-in, queue, approval, applicant sign-in, own campus only, no queue access). Screenshots: sign-in, registration and
  campus request at 1440/820/390 px, light and dark, no overflow.
- **Tooling note:** a `ruff --fix` run with ruff's default rule set (the backend has no ruff configuration) rewrote
  formatting in about 70 existing files. Every one was checked against its compiled bytecode from before the change:
  all identical except one equivalent import in `test_cv_pipeline_e2e.py`, which was restored. Lint for this step used
  the project's usual `--select I,F,E` without `--fix`.
- **Open:** decision H (password + fresh code to approve), full deletion on rejection (decision I vs page 15 history),
  tombstones (J), and a Main Admin web page for the queue.

## 2026-11-13 — Auth parity audit, compulsory first-campus name, Forgot Password page (D-021, migration 0022) — implemented, awaiting review

- **Backend:** `POST /auth/organization-registrations` requires `campus_name` (2–100 characters, letters or digits);
  migration 0022 adds `organization_registrations.campus_name` and replaces `app_org_registration_submit` /
  `_complete` (and the two expiry helpers) so the first campus gets that name instead of "Main Campus". Pending
  registrations from before the migration keep "Main Campus". Downgrade restores migration 0004's functions.
- **Campus requests (D-020):** re-audited; the exact organization name and the Organization ID must match the same
  active organization (unchanged; documented why they cannot drift before the code step).
- **Web:** campus field and a completion screen with the Organization ID and Main Admin ID (copy buttons);
  sign-in opens `/dashboard` (was `/personnel`) and links to the new `/forgot-password` page (Organization ID or
  Supervisor ID + email → code → one-time token → new password); the auth top bar is no longer sticky.
- **Tests:** core_api **641 passed, 0 failed** (635 before); ruff (I, F, E) clean; mypy no new errors. Web **314
  passed** (309 before); `tsc` clean; build OK. Live: new `website/smoke/auth_smoke.py` 22/22, personnel 31/31,
  operations 36/36, site 40/40, staff 32/32 (the smoke scripts now send `campus_name`).
- **Docs:** `api_contracts.md` (auth table), `decision_log.md` (D-020, D-021), `organization_registration.md`,
  `FYP_Documentation/` context, requirements copy (Section 8 amendments) and architecture. The originals in
  `backend/docs/requirements/` are unchanged.

## 2026-11-15 — ML training pipelines and cv_inference ONNX adapters (D-022) — built, nothing trained yet

**`ml_training/`** (tested on CPU with synthetic data; no dataset downloaded, no GPU run):
- **Weapon detection:** `ultralytics` YOLO11 / RT-DETR trainer (`train_weapon_detector.py`, shared with
  `train_human_detector.py`), VOC / COCO / YOLO parsers and converter with hashed deterministic splits and kept
  negatives, CCTV augmentation settings; own metrics (per-class P/R/F1, AP@0.50, AP@0.50:0.95, false positives
  per 1,000 negative frames, latency) applied to `.pt` and to the exported ONNX through a NumPy decoder.
- **Fight / violence:** `decord` clip reader (OpenCV fallback) with frame-rate normalisation and sliding
  windows; RWF-2000 / RLVS / UCF-Crime layouts; clip-consistent CCTV augmentation; Video Swin-T / S, MViT v2-S,
  R(2+1)D-18 from torchvision (Kinetics-400); bf16/fp16 AMP, cosine + warm-up, balanced sampling, best/last
  checkpoints, resume; clip- and video-level metrics with false-alarm rate; threshold chosen on val.
- **Re-ID:** OSNet-x1.0 implemented (torchreid-compatible names) and ResNet50-IBN-a, BNNeck 512-D; CE with label
  smoothing + batch-hard triplet + centre loss; PK sampler; Market-1501 / MSMT17 / CUHK03 parsers; held-out
  training identities for validation; Market-protocol CMC / mAP; cosine bands calibrated to target precisions.
- **Export:** `export_to_onnx.py` for all three (opset 17, dynamic batch, onnxsim, parity < 1e-4 at batch 1 and
  2, `<name>.manifest.json` with pre-processing, labels, thresholds, dataset, commit, metrics, SHA256). Video
  Swin needs a constant attention mask at export (`export_friendly_swin`); pytorchvideo was rejected because it
  does not import with torch 2.x.
- **Tests:** 30 passed (`ml_training/tests`), including end-to-end train → export → ONNX evaluation for the fight
  and Re-ID pipelines and an untrained YOLO11n export.

**`services/cv_inference/`:** `src/manifest.py` (checksum / task / shape checks), `WeaponOnnxDetector`,
`ViolenceOnnxClassifier` + `FrameRingBuffer` + `TemporalDetectorAdapter`, `CompositeDetector`,
`PersonReIdOnnxEmbedder` + `CropQualityFilter` + `ReIdMatcher` + `ReIdTrackObserver`, `CampusGallery` (Faiss
inner product, NumPy fallback, time window, same-camera exclusion), `CV_ENABLE_*` switches, YAML settings
(`CV_CONFIG_FILE` / `--config`), `CV_DEVICE` cpu / cuda / tensorrt / auto, `--detector auto`, `--campus-id`.
Re-ID observations go to a JSONL file: Core API has no Re-ID intake. Tests: 75 passed (57 before + 18) on
generated ONNX graphs (`tests/fixtures/make_dummy_models.py`).

**Not done / honest limits:** no real dataset, no trained model, no accuracy figure, no GPU measurement; the
default manifest names in `config.py` point at files that do not exist yet; Re-ID storage, backend intake and
the Supervisor review page are not built; `ultralytics` is AGPL-3.0 (enterprise licence needed for commercial
use). Docs: `ml_training/README.md` (GPU runbook), `datasets/README.md`, `model_registry.md`,
`cv_inference/README.md`, `weights/README.md`, project context §6, architecture Part A / B / 6.

## 2026-11-17 — Video ingestion, live monitoring, recordings, retention and the evidence vault (D-023, migration 0023) — implemented, awaiting review

- **Migration 0023:** `camera_status`, `campus_retention_settings`, `recording_segments`, `evidence_items`,
  `storage_status` (RLS on all; the app role gets only what each page needs; the platform role nothing);
  notification kinds `CAMERA_OFFLINE`, `CAMERA_ONLINE`, `STORAGE_WARNING`, `STORAGE_CRITICAL` and a `camera_id`
  target; seven SECURITY DEFINER functions for the services (camera list, status report with notifications,
  segment registration confined to the camera's folder, expiry list, purge, disk report, storage status).
  Upgrade → downgrade → upgrade tested (`test_migration_0023.py`).
- **core_api:** `/live/*` (status, audited live view, newest frame, connection test), `/recordings*` (timeline with
  purge times and AI markers, playback with byte ranges, audited), `/recordings/retention` (view; change by Admins
  and Supervisors with their own password), `/evidence*` (save from alert or playback with ffmpeg, list with
  filters, view, edit, Admin-only deletion with password), `/internal/*` with `INTERNAL_SERVICE_TOKEN`. Storage
  tiers in `LocalCampusMediaStorage` (`recordings/`, `evidence/`, `live/`); campus deletion stages every tier.
  Reusable `PasswordConfirmation` (same lockout as sign-in).
- **`services/video_ingestion`** (was placeholders): ffmpeg per camera → segments, live frame, CV spool; RTSP,
  ONVIF reported Unknown, recorded-file fallback; health monitor; registry diffing; 13 tests including a real ffmpeg
  run. **`services/storage_retention_daemon`** (was placeholders): API-driven purge confined to `recordings/`,
  spool cleanup, disk report; 4 tests. **`cv_inference`:** `SpoolFrameSource` and `--spool-dir` (76 tests).
- **Web:** "Video & Evidence" navigation group — Live Cameras (grid 1×1–4×4 with pages, fullscreen, search,
  location and status filters, error tiles, counters, open-alert badges), Recordings (zoomed timeline with markers,
  filters, player with mark in/out, save clip/snapshot, purge countdown, storage panel by tier, retention dialog),
  Evidence Vault (filters, viewer, details editor, deletion dialog); Cameras page status column and Test action;
  dashboard "Cameras online" tile; "Save as evidence" on the alert page; notification labels and links.
- **Corrections during the work:** retention was first built Admin-only and storage warnings went to Admins only;
  both contradicted requirements 17 / the Supervisor spec and were changed before finishing. The live page's first
  "AI monitoring" badge (an unverifiable claim) was replaced with badges from open alerts. One pre-existing file,
  `branch_repository.py`, was reformatted by an over-broad `ruff format` and restored to its exact original layout
  (verified against its compiled bytecode positions). `.env.example` advertised `STORAGE_WARNING_THRESHOLD_PERCENT`,
  which the API never read; it now lists the real `STORAGE_WARNING_PERCENT` / `STORAGE_CRITICAL_PERCENT`.
- **Tests:** core_api **650 passed** (641 before this module; RLS isolation, Guard 403, other campus 404, platform
  role denied on every new table and function, migration round trip); ruff (I, F, E) clean; mypy no new errors (the
  11 reported are pre-existing: openpyxl stubs, `branch_repository`). Web **328 passed** (314 before), `tsc` clean,
  build OK. Live: new `website/smoke/video_smoke.py` **43/43** with the real ingestion service and retention daemon
  (7 segments ingested, evidence cut, 1-minute retention purge removed the routine files and left the evidence
  intact). Screenshots light/dark at 1440 / 820 / 390: no page-level horizontal overflow.
- **Not done / honest limits:** no physical camera connected; live view is the newest frame each second, not
  video-rate streaming; no detection overlays; ONVIF ingestion needs stored credentials (not built); Supervisor
  evidence deletion awaits the owner's decision; per-campus storage thresholds, Azure storage, an audit-log viewer,
  Incidents, Dockerfiles / compose entries for the two services are not built; platform health still lists
  `video_ingestion` as not deployed (it has no measurement for it).
- **Docs:** `decision_log.md` (D-023), `api_contracts.md`, `role_permission_matrix.md`, `.env.example`, service
  READMEs, `website/smoke/README.md`, `FYP_Documentation/` context, architecture and the Admin / Supervisor page
  copies. The originals in `backend/docs/requirements/` are unchanged.

## 2026-11-18 — Remaining Organization Admin and Supervisor features (D-024, migrations 0024–0029) — implemented, awaiting review

- **Migrations:** 0024 audit-log access (category, outcome, read policies; still no UPDATE/DELETE), sign-in / reset audit
  functions, step-up consumption, campus-request erasure on rejection; 0025 incidents, case events / participants /
  chat, evidence tags, case link and location copy, manual-report case link (and the fix of the 0023 composite
  `ON DELETE SET NULL` foreign keys that nulled tenant columns); 0026 organization settings, alert handler,
  automatic escalation, assistance broadcast (deferred constraint trigger), camera status history; 0027 campus /
  camera AI settings and worker status; 0028 pgvector Re-ID observations, person searches and candidates; 0029 Admin
  profile changes, ID regeneration, rejoining, permanent deletion, user status history. Each tested upgrade →
  downgrade → upgrade.
- **core_api:** routers `audit_log`, `branch_approval` (rewritten: Main Admin, password + code to approve), `incident`
  (+ `/guard/incidents`), `alert` (escalations, handler, snapshot, clip, internal escalate-overdue), `evidence`
  (tags, case, location filters), `ai_config` (+ live overlay, internal ai-config / ai-status), `organization_settings`,
  `person_search` (+ internal Re-ID intake / purge), `profile` (Admin), `user_records` (+ Guard ID regeneration),
  `analytics` (report, CSV / PDF export, dashboard feed); `/auth/me` adds `account_id` and `is_main_admin`; Super Admin
  tokens get `403 SUPER_ADMIN_NOT_ALLOWED`; a background task runs automatic escalation; CSV exports are formula-safe;
  PDF via ReportLab; optional ONNX Re-ID embedder for photo search (`REID_MODEL_MANIFEST`).
- **Services:** `cv_inference` — `AiConfigWatcher` + `GatedDetector`, `OverlayWriter` (`--storage-root`),
  `HttpObservationSink` (`CV_REID_SINK=core_api`), status reports; `storage_retention_daemon` — Re-ID observation purge.
- **Web (`website/`):** new pages Campus Requests, Audit Logs, Incidents (list, detail with links, history, chat),
  Person Search (search, results review), AI Configuration, Reports & Analytics (charts with table view, CSV / PDF),
  Profile, Organization Settings, User Records; extended Alerts (search, dates, handled by me, Guard counts, handler,
  snapshot / clip, escalations, linked cases, Guard search when escalating), Evidence (tags, case, location filters,
  case link / unlink), Live (AI overlay with a switch), Dashboard (spec counters, feeds, charts, live preview, Guard
  availability, activity, AI summary, urgent banner, quick actions), Locations (cameras and Stationed Guards tables),
  Import History (saved mappings), Staff (New Guard ID); navigation groups "Reports & oversight" and
  "Account & organization" (Campus Requests only for the Main Admin); vendor code split into its own chunk.
- **Corrections during the work:** the Campus Requests page called the API for non-Main Admins (the server refused it)
  — now nothing is requested; the alert snapshot was hidden when the detector sent none although the API falls back
  to the recorded frame — now always tried; the similarity shown on results is truncated, not rounded, so it never
  appears to cross a band; checkbox labels and in-card controls had wrapping / nested-box styles — fixed before the
  screenshots; the earlier mypy messages (`branch_repository` rowcount, missing openpyxl stubs) are resolved.
- **Tests:** core_api **696 passed, 0 failed** (650 after D-023; new `test_audit_logs.py`, `test_campus_request_approvals.py`, `test_incidents.py`, `test_alert_operations.py`, `test_person_search.py`, `test_staff_accounts.py`, `test_analytics.py`, `test_migration_0024.py`, `test_migration_0025_0029.py`, unit tests for campus approvals; existing tests updated for the 403 Super Admin answer, readable audit rows and the new `/auth/me` fields) on a scratch PostgreSQL 16 cluster; ruff (I, F, E; 120) clean; mypy **no issues** (282 files). cv_inference 80 passed, storage_retention_daemon 5. Web **345 passed** (328 before; new `src/test/d024.test.tsx` with 17 tests; navigation tests updated), `tsc` clean, production build clean (vendor chunk split). Screenshots: 25 page states × light / dark × 1440 / 820 / 390 = 150 captures in `FYP_Documentation/screenshots/admin-supervisor-pages/`, **0 with page-level horizontal overflow** (`overflow-report.json`), against a seeded scratch database through the real API.
- **Not done / honest limits:** see D-024 item 12 — no AI investigation assistant or RAG, no trained weapon / fight /
  Re-ID weights (photo search unavailable), no physical camera, no re-analysis of stored recordings for Person Search, no
  billing; Supervisors still cannot delete evidence. The seed and capture scripts used for the screenshots were scratch
  tools and are not part of the repository.
- **Docs:** `decision_log.md` (D-024), `api_contracts.md`, `role_permission_matrix.md`, `.env.example`, `cv_inference` and
  `storage_retention_daemon` READMEs, `FYP_Documentation/` (proposal status, project context §8J, architecture, Admin and
  Supervisor page status tables). The originals in `backend/docs/requirements/` are unchanged.

## 2026-11-19 — Guard mobile app, real-time WebSocket, LiveKit voice calls, persistent alarm, push tokens (D-025)

- **Migration 0030** (`0030_realtime_calls_push.py`): `call_sessions`, `call_participants`, `device_push_tokens`,
  `user_notifications.push_claimed_at`; functions `app_comm_target_visible`, `app_alert_responders`, `app_call_invitees`,
  `app_device_token_register` / `_forget`, `app_push_claim`; triggers ending calls with their alert / case and the
  `pg_notify('cctv_realtime')` signal triggers (`app_rt_*`, IDs only). RLS on the new tables; the platform role has no
  privilege. Tested upgrade → downgrade → upgrade.
- **core_api:** `authenticate_tenant_token` extracted from `get_current_actor` so REST and the WebSocket share every
  session rule; `presentation/realtime/hub.py` (one LISTEN connection per process, per-connection send queues, RLS
  visibility check for Guards, re-checks on `session.revoked` / `organization.status` and every ping);
  `realtime_router` (`WS /realtime/ws`, `GET /realtime/status`); `call_router` + `CallService` + `SqlCallRepository`
  (start-or-join, invitations, decline, leave, end, audit `ALERT_CALL_*` / `INCIDENT_CALL_*`); `device_token_router`;
  `GET /alerts/{id}/responders`; `GET /guard/profile` adds `campus_name`, `status`, `operational_mode`;
  `infrastructure/voice` (LiveKit token signing with PyJWT, best-effort `DeleteRoom`, disabled provider → `503
  VOICE_CALL_NOT_CONFIGURED`); `infrastructure/push` (FCM HTTP v1 with a service-account RS256 assertion and cached access
  token; no-op sender) and `background/push_dispatch.py` (claim → send → forget dead tokens). New settings in
  `.env.example` (they replace the unused `FIREBASE_CREDENTIALS_PATH` placeholder). No new Python dependency.
- **mobile/ (new):** Expo SDK 58 / React Native 0.88 / TypeScript / Expo Router / TanStack Query. Screens 1–10, the
  notifications screen, the alarm overlay, incoming-call card and active-call overlay; API client with forced sign-out;
  WebSocket client with back-off and polling fallback; alarm engine (`expo-audio` looping siren generated for the project,
  repeating vibration, `security-alarm` Android channel); LiveKit adapter (`@livekit/react-native`, `livekit-client`,
  Expo config plugins) that degrades without native WebRTC; offline outbox (AsyncStorage + photo files, owner-tagged,
  batched `/guard/sync`); push-token registration. Colours sampled from the owner's reference designs (only colours).
  The scaffold's nested `.git` and its `.claude/settings.json` (a plugin switch) were removed.
- **Corrections during the work:** the migration's downgrade first dropped functions still used by the new tables'
  policies — tables are now dropped first; ESLint's React-compiler rules found refs read during render (alarm engine,
  voice client, current call) and `Date.now()` in render — moved to state initializers / effects; a removed `splash`
  key and two missing peer dependencies (`expo-font`, `expo-asset`) were found by `expo-doctor`; TypeScript 6 no longer
  loads `@types/*` by default, so `tsconfig.json` names `jest`.
- **Tests:** core_api **725 passed, 0 failed** (696 before; new `tests/integration/test_realtime_calls_push.py` with 15
  tests — responders, profile context, device tokens and deactivation, Super Admin 403s, push claiming once and dead
  tokens, calls without a provider, LiveKit audio-only tokens and invitations, call end rules and audit, call end on
  alert resolve, case calls, WebSocket sign-in, delivery only to allowed users, `ASSIGNMENT_CHANGED` and suspended
  organization ending sockets, call invitations over the socket — `tests/integration/test_migration_0030.py`, and
  `tests/unit/test_voice_and_push.py` with 13 tests); ruff (I, F, E; 120) clean; mypy no issues (300 files). Mobile:
  **68 Jest tests in 12 suites** (client, socket, outbox, units, auth, alarm, dashboard, chat, calls, reports, profile,
  notifications), `tsc --noEmit` clean, `expo lint` clean, `expo-doctor` 20/20, `expo export --platform android` bundle
  built (the siren included).
- **Not done / not verified:** Admin / Supervisor web UI for calls and the WebSocket (next phase); no physical handset,
  no live LiveKit room, no live FCM delivery (need a development build, LiveKit keys and a Firebase project); APNs for
  iOS; push for call invitations; access tokens still have no refresh.
- **Docs:** `decision_log.md` (D-025), `api_contracts.md`, `role_permission_matrix.md`, `guards.md`, `.env.example`,
  `mobile/README.md`; `FYP_Documentation/` (Guard app status table, new requirements and full SRS contract, project
  context, backend architecture, proposal status). The originals in `backend/docs/requirements/` are unchanged.

## 2026-11-19 — Web real-time messaging and voice calls for Admins and Supervisors; Web ↔ Mobile parity (D-026, migration 0031)

- **Migration 0031** (`0031_realtime_case_events.py`): `trg_rt_incidents` (status changes only) replaced by
  `trg_rt_incident_events` on `incident_events` — one `incident.updated` signal per case history entry, with `kind` and
  `state`. Tested upgrade → downgrade → upgrade.
- **core_api:** `CallSession` / `CallSessionResponse` gain `target_title` and `target_location` (joined from the alert or
  case and its building, floor and area in `SqlCallRepository`); no route, permission or existing field changed.
- **website/:** `src/realtime/` (`RealtimeSocket` client, `RealtimeProvider`, `useLiveRefresh`, `ConnectionStatus`);
  `src/features/calls/` (`callsApi`, `CallProvider`, `VoiceClient` with a lazily loaded `LiveKitVoiceClient`, Web Audio
  ringer, `CallControls`, `IncomingCallBanner`, `CallDock`); live refresh wired into Alert details, Alert chat, Case
  details, Case chat, Alerts and Incidents lists, Dashboard, topbar bell and Notifications; Start / Join call cards on
  alert and case details; Live / Reconnecting / Polling indicator in the topbar; sign-out on `403 ORGANIZATION_INACTIVE`
  with a notice on the sign-in page; `vite.config.ts` proxies WebSocket upgrades and puts `livekit-client` in its own
  lazy chunk (eager JavaScript stays ~0.7 MB instead of ~1.3 MB). New dependency: `livekit-client` 2.24.
- **mobile/:** the incoming-call card and the call overlay show `target_title · target_location` (types optional).
- **Corrections during the work:** the web shell started asking `GET /calls`, so a characterization test that lists
  the shell's requests now ignores it; `livekit-client` was first bundled into the eager vendor chunk; the call dock
  could cover the end of a page (the page now gets padding equal to the dock's measured height); a second tab joining the
  same call would have made the first tab leave on the server (LiveKit's duplicate-identity disconnect is now told
  apart). Found during the live smoke: campus approval now needs the Main Admin's authorization (D-024).
- **Tests:** core_api **730 passed, 0 failed** (725 before; new `tests/integration/test_realtime_web_parity.py`, 5
  tests); ruff (I, F, E; 120) clean; mypy no issues. Web: **31 new Vitest tests** (`src/test/d026.test.tsx`: WebSocket auth / heartbeat / back-off / session end,
  Live / Reconnecting / Polling, polling fallback, sign-out on `ORGANIZATION_INACTIVE` via socket and REST, live alert
  chat, live Guard actions, live case chat and case updates, instant bell and resync after reconnecting, ringing on
  `call.invited`, blocked ringtone, invitation found by polling, join / mute / deafen / leave, start from the alert page
  without organization or campus IDs, end for everyone, `can_end` false, ended by someone else, joined elsewhere,
  connection lost, leave on sign-out, join from a case, `VOICE_CALL_PROVIDER=none`, `503 VOICE_CALL_NOT_CONFIGURED`,
  microphone denied, no WebRTC, both staff roles) — **376 web tests** in total, `tsc` clean, production build clean.
  Live smoke `website/smoke/realtime_comms_smoke.py`: **45/45** through the Vite proxy (Guard, second Guard, Admin,
  Supervisor and another campus's Admin at once; Super Admin refused). Mobile: 68 Jest tests, `tsc`, `expo lint` clean.
  Screenshots (26, 0 px horizontal overflow): `FYP_Documentation/screenshots/web-realtime-comms/`.
- **Not done / not verified:** live LiveKit audio (no server or keys; the dock screenshots use a LiveKit stand-in);
  Firefox / Safari; production reverse-proxy WebSocket forwarding; physical handset. `operations_smoke.py` fails one
  check that predates this work: since D-024 the "Additional Guards required" assessment broadcasts to the campus, so
  its later campus escalation finds no new Guard (35/36; not changed here).
- **Docs:** `decision_log.md` (D-026), `api_contracts.md`, `role_permission_matrix.md`, `guards.md`,
  `website/smoke/README.md`; `FYP_Documentation/` (Admin and Supervisor page specs, Guard spec status and SRS, project
  context, backend architecture, proposal status). The originals in `backend/docs/requirements/` are unchanged.

## 2026-11-19 — Read-only AI Investigation & Security Analytics Chatbot (D-027, migration 0032) — built & CPU-tested

- **ai_rag_and_agent:** the 30 empty RAG / LangGraph / approval placeholder files and the empty Dockerfile were removed
  (that design was dropped by D-027). New package `assistant_engine` (pyproject, `py.typed`; installed into the backend
  and ml_training venvs with `uv pip install -e … -C editable_mode=compat` so mypy follows it): catalogue of 13
  read-only tools, date periods, deterministic planner with follow-up context, guardrails, grounded synthesizer,
  providers (deterministic / Ollama / OpenAI-compatible, injectable HTTP transport), prompts (Qwen ChatML), engine.
- **core_api:** migration 0032 (`ai_chat_sessions`, `ai_chat_messages`, RLS owner-only, `app_ai_chat_purge`,
  forget-user trigger, `app_audit_category` → `AI_ASSISTANT`); settings `AI_LLM_*` and `AI_CHAT_RETENTION_DAYS`
  (+ `.env.example`); `assistant_repository.py` (chat storage, campus vocabulary, the 13 tools — explicit org / campus
  filters on top of RLS, each tool in a savepoint, D-024 footage-gap logic reused), `assistant_use_cases.py`,
  `assistant_dto.py`, `assistant_router.py` (+ internal purge route), `infrastructure/assistant/factory.py`, the audit
  category `AI_ASSISTANT` (domain enum; Supervisors may filter their own). Test client factory accepts an engine.
- **storage_retention_daemon:** `purge_chat_sessions()` each pass; `chat_sessions_removed` in the report.
- **ml_training/chatbot:** configs `chatbot_qwen25_7b_qlora.yaml` / `chatbot_qwen25_14b_qlora.yaml`,
  `dataset_builder.py`, `pii.py`, `formatting.py`, `world.py`, `metrics.py`, `evaluate_chatbot.py`,
  `train_chatbot_qlora.py`, `export_to_ollama.py`, `README.md` (RTX 5080 runbook), `requirements-chatbot.txt`.
- **website:** `features/assistant/` (types, API, `AnswerCard`/`MessageView`, `AssistantPage`, `InvestigatePanel`,
  route `/assistant`), nav item "AI Investigation" for Admins and Supervisors, "Investigate with AI" on Incident Detail,
  `AI_ASSISTANT` label on Audit Logs, chatbot styles (`components.css`).
- **Corrections during the work:** intent patterns were case-sensitive; an incident-scoped chat turned general
  questions into case investigations; "gate-breach" matched the place "Gate" (hyphens now belong to a word);
  `related_events` needed explicit parameter casts for asyncpg; the read-only guard missed requests starting with
  "Hey," / "I need you to"; the training-set builder first skewed the test split (de-duplication) and then had no
  follow-ups in it (split key now includes the follow-up's context); seeding screenshots with an open owner
  transaction deadlocked the API (autocommit).
- **Tests:** core_api `tests/integration/test_assistant.py` **13 tests** (status and 403s; trends, peaks and
  comparisons against SQL; hotspots, follow-up and camera reliability; case timeline, related events, gaps and evidence
  search; Guard response metrics against SQL; manual-report search and missing data; read-only and no-accusation;
  Supervisor exclusions and audit; chat privacy and deletion; 30-day purge, permanent-deletion trigger and campus
  erasure; RLS and platform role; mocked Ollama used / unreachable; migration 0032 round trip) — full suite **743
  passed, 0 failed** (730 before); ruff (I, F, E; 120) and mypy clean. Engine **32 tests**; daemon **7** (2 new); ml_training **39** (9 new); web **383** (7 new in
  `src/test/d027.test.tsx`, nav expectations updated), `tsc` and production build clean. Live smoke
  `website/smoke/chatbot_smoke.py` **27/27**. Screenshots: `FYP_Documentation/screenshots/ai-chatbot/`.
- **Not done / not verified:** no RTX 5080 QLoRA run and no Qwen weights downloaded; no live Ollama model (the Ollama
  path is tested with a mocked server); accuracy on real campus questions; the deterministic baseline figures are on
  synthetic questions only.
- **Docs:** `decision_log.md` (D-027 answers and built status), `api_contracts.md`, `role_permission_matrix.md`,
  `ai_rag_and_agent/README.md`, `ml_training/chatbot/README.md`, `storage_retention_daemon/README.md`, website and smoke
  READMEs; `FYP_Documentation/` (Admin and Supervisor specs incl. the D-027 SRS, project context, backend
  architecture, proposal). The originals in `backend/docs/requirements/` are unchanged.


## D-028 — AI feature controls (2026-11-24; implemented, awaiting review)

- **core_api migration 0033** (`0033_ai_feature_controls.py`, reversible): `platform_organization_ai_blocks` (FK
  `organizations` ON DELETE CASCADE, RLS enabled, no grants) with `platform_organization_ai_permissions_get` / `_set`
  (platform role only; blocking also switches the module off on every camera of the organization) and
  `app_organization_ai_blocks()` (app role; own organization, Admins and Supervisors); `campus_ai_settings`
  `supervisor_can_manage_*` (default true); `camera_ai_settings` defaults false; `app_camera_ai_config` = platform AND NOT
  blocked AND campus AND `COALESCE(camera, false)`; existing cameras back-filled with every module (no behaviour change).
  Downgrade restores the 0027 function and defaults.
- **core_api code:** `ModuleSet.without / keeping / with_changes / of_keys`, `FEATURE_NAMES`, `CORE_MODULES`,
  `module_key`; `AiConfiguration` four-level view (`available_modules`, `org_blocked`, `blocked_by_platform`,
  `supervisor_visible_modules`, Supervisor masking), campus visibility, `update_cameras_bulk`, `resolve_for_camera` /
  `save_for_camera` / `camera_modules` (camera create / edit in one transaction through `ICameraAiLink`); bulk route
  registered before `/cameras/{camera_id}`; `CameraRequest.ai_modules`, `CameraResponse.ai_modules /
  ai_effective_modules`; platform `GetOrganizationAiPermissions`, `ChangeOrganizationAiPermission` (SA-SEC-4,
  `ORGANIZATION_AI_PERMISSIONS_UPDATED`), `ai_permissions` on organization details, `GET|PUT …/ai-permissions`.
- **cv_inference:** `modules_served`, `GatedDetector` runs each composite part only when one of its modules is allowed
  (reset of the fight buffer once when it stops), Re-ID-only persons kept for crops; `OverlayWriter` draws only allowed
  modules. New `tests/test_model_skipping.py` (7 spy tests).
- **website:** `AiFeaturesFieldset` (Add / Edit Camera), `BulkAiDialog`, AI pills, row selection, Select all visible,
  counter, `SupervisorAccessPanel`, blocked notices; AI dialogs never re-send blocked / platform-disabled modules.
  **super_admin:** `AiAccessCard` + `AiPermissionDialog`, statuses ALLOWED / BLOCKED / DISABLED_GLOBALLY, API types.
- **Existing tests updated for default-OFF (no assertion removed or weakened):** `test_alert_operations.py` (the camera
  is opted in explicitly, and the default-off state is asserted first); `test_person_search.py` (cameras opted into
  Re-ID by an autouse fixture; the "disabled camera" case now switches Re-ID off with an UPDATE).
- **Tests:** core_api `test_ai_feature_controls.py` (5) and `test_migration_0033.py` (1) — full suite
  **749 passed, 0 failed, 0 skipped (743 before; 6 new)**; ruff (I, F, E; 120) and mypy clean. cv_inference **88 passed** (7 new). web **391** (8 new in
  `src/test/d028.test.tsx`), `tsc` and build clean. super_admin **186** (3 new in `src/test/d028.test.tsx`), `tsc` and
  build clean. Live smoke `website/smoke/ai_controls_smoke.py` **27/27**; `super_admin/smoke/platform_smoke.py`
  **32/32** (now also checks `ai_permissions` and the block / unblock route). Screenshots:
  `FYP_Documentation/screenshots/ai-feature-controls/` (7 views × light/dark × 1440/820/390 = 42, 0 px overflow).
- **Not verified:** model skipping with real ONNX weights on a live camera (spy-tested; no trained weapon / fight /
  Re-ID weights exist yet).
- **Corrections during the work:** `CampusAiSettings` field order (positional construction); the bulk test's stream
  addresses had spaces; the smoke's second-campus camera used the first campus's location; a full-page screenshot after
  `scrollIntoView` drew the sticky header mid-page.
