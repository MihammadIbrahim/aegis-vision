# Platform Super Admin authentication

The Super Admin logs in with a username and password at `POST /api/v1/platform/auth/login`.
There is no email OTP and no email password reset for this role (requirement 9.1). Two kinds
of credentials are accepted by that same endpoint:

| | Normal credentials | Emergency recovery credentials |
| --- | --- | --- |
| Where they live | Database table `platform_admins` (password as an Argon2id hash) | Server environment only: `SUPER_ADMIN_RECOVERY_USERNAME`, `SUPER_ADMIN_RECOVERY_PASSWORD` |
| How they are set | Seed command on first creation, then the dashboard API | Editing the server's `.env` and restarting the API |
| How they change | `POST /api/v1/platform/me/username`, `POST /api/v1/platform/me/password` (current password required) | Only by changing the environment variables |
| Which account they open | The account with that username | Always the primary Super Admin, with exactly the normal Super Admin permissions |

Both methods share one lock: 5 failed attempts within 15 minutes lock the account for
15 minutes, whichever method was tried (`PLATFORM_LOGIN_MAX_ATTEMPTS`,
`PLATFORM_LOGIN_LOCKOUT_MINUTES`). Wrong usernames and wrong passwords of either kind get the
same reply: `401 Invalid username or password.`

## Initial setup

1. Put the initial values in `backend/.env` (`SUPER_ADMIN_USERNAME`, `SUPER_ADMIN_PASSWORD`).
2. Run `python -m src.entrypoints.seed_super_admin` from `backend/services/core_api`.
   It creates the primary Super Admin once. Running it again changes nothing, even after the
   username or password were changed through the API. Nothing is reset when the API starts.
3. `--reset-password` sets the primary account's username and password back to the initial
   values, unlocks it, and signs out every session. It needs the database owner credentials,
   so only someone with server access can run it.

## Changing the normal credentials

Changing the username or password needs the current **database** password. A successful change
increases the account's token version, so every earlier token (normal or recovery) stops
working with `401 Session expired. Please log in again.`; the response carries a fresh token.
The recovery credentials are not affected.

## Emergency recovery

- Recovery credentials are read once, when the API starts. **Restart the API after changing them.**
  Leaving both variables empty turns recovery off.
- They are compared in constant time, never stored in the database, never logged, and never
  returned by any endpoint.
- Every login is written to `platform_audit_logs` with `metadata.login_method` set to `NORMAL`
  or `RECOVERY`. No username typed at login, password, or token is recorded.

**Security warning.** Anyone who can read the server's `.env` can log in as the Super Admin with
the recovery credentials. This is an emergency mechanism, not a substitute for protecting the
server and its secrets. Use long, unique values outside local development; `admin` / `admin@123`
are development defaults only.

## Recovering forgotten normal credentials

If the normal username or password is forgotten:

1. Log in at `POST /api/v1/platform/auth/login` with the emergency recovery credentials. The token
   records that this is an emergency (`RECOVERY`) session.
2. Call `POST /api/v1/platform/recovery/credentials` with that token, entering the emergency
   credentials again plus the new normal username and password:

   ```json
   {
     "recovery_username": "admin",
     "recovery_password": "<emergency password>",
     "new_username": "restored.admin",
     "new_password": "<new password>",
     "confirm_new_password": "<new password>"
   }
   ```

Rules:

- Only a session opened with the emergency credentials may call it (a normal-login session gets
  `403`), only for the primary Super Admin, and only while recovery is configured on the server.
- The new username must be valid and unique. The new password must follow the normal policy
  (12-128 characters, a letter and a digit, not containing the username) and must differ from the
  emergency password.
- Wrong re-entered emergency credentials return `403` and count toward the shared lock.
- Success changes both values in one step, signs out every earlier Super Admin token (normal and
  emergency), and returns a fresh emergency-session token. The emergency credentials do not change.
- Each attempt is written to `platform_audit_logs` as `CREDENTIALS_RECOVERED` (`SUCCESS` or
  `FAILURE`), without any password, recovery secret or token.

`seed_super_admin --reset-password` on the server remains a last resort if recovery is turned off.

## Platform management (decision D-019)

Besides signing in and managing their own credentials, the Super Admin now has the platform overview,
organization management (list, details, Main Admin contact, status), platform AI settings, system health and
the audit-log viewer. Changes there also need the current **database** password and share the login lock
described above. The full contract, including every field name, is the SRS section at the end of
`FYP_Documentation/FYP_Final_Super_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt`; the decision is
D-019 in [`decision_log.md`](decision_log.md).
