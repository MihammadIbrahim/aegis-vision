# Supervisor creation, Supervisor sign-in, password reset

Decisions M–P in [`requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md`](requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md).
Migration `0007_supervisors_and_password_reset`. Errors use `{"detail", "code"}`, as in
[organization_registration.md](organization_registration.md).

## Supervisor creation (Admin token required)

| Step | Endpoint | Body | Result |
|---|---|---|---|
| 1 | `POST /api/v1/organization-admin/supervisors/authorization` | `{"current_password"}` | Code emailed to the Admin; `{"id", ...}` |
| | `.../authorization/{id}/resend-otp` | — | New code |
| 2 | `.../authorization/{id}/verify` | `{"otp"}` | `{"authorization_id", "form_expires_at"}`; the form may be submitted once |
| 3 | `POST /api/v1/organization-admin/supervisors` | `{"authorization_id", "full_name", "username", "email", "phone_number", "password"}` | `201`; code emailed to the Supervisor |
| | `.../creations/{id}/resend-otp` | — | New code |
| 4 | `.../creations/{id}/verify` | `{"otp"}` | `201` `{"supervisor_id" (27 chars), "full_name", "username", "email", "phone_number", "role", "organization_id", "campus_name", "created_at"}` |

Rules:
- **Who:** only active Admins of active organizations can do any of this. Supervisors and Guards get
  `403`; Super Admin tokens get `401`.
- **Wrong current password:** `403 CURRENT_PASSWORD_INCORRECT`. It counts toward the same lock as
  sign-in.
- **Missing, unverified, used or another Admin's confirmation:** `403 REVERIFICATION_REQUIRED`.
- **Another Admin's form:** `404`.
- **Taken username:** refused at step 3.
- **Taken email:** refused at step 4, and nothing is created.

## Supervisor sign-in

Supervisors use the same endpoints as Admins (`/api/v1/auth/organization-admin/login`, see
[organization_admin_login.md](organization_admin_login.md)). After the token:
- `GET /api/v1/auth/me` returns `{"user_id", "full_name", "role", "dashboard"}`.
- `GET /api/v1/supervisor/dashboard` is for Supervisors only; the Admin dashboard refuses Supervisors.

## Forgot password (Admins and Supervisors; public)

| Step | Endpoint | Body | Result |
|---|---|---|---|
| 1 | `POST /api/v1/auth/password-reset` | `{"organization_id", "email"}` | `202`, the same whether or not an account matches |
| | `.../{reset_id}/resend-otp` | — | `202` |
| 2 | `.../{reset_id}/verify` | `{"otp"}` | `{"reset_token", "expires_in"}` (one-time) |
| 3 | `.../{reset_id}/complete` | `{"reset_token", "new_password", "confirm_new_password"}` | Password changed |

What protects it:
- **Code limits** are the same as sign-in: expiry, attempts, cooldown and an hourly limit. There is
  also a per-IP limit (`PASSWORD_RESET_IP_LIMIT_PER_HOUR`).
- **Same answers for unknown accounts:** a request with no matching account is stored and behaves
  identically (codes are "issued" and wrong guesses counted), but no email is sent.
- **After the reset:**
  - tokens issued before the change are refused (`401 Session expired`);
  - pending sign-ins are cancelled;
  - any lock is cleared.

## Settings

All optional; see `backend/.env.example`:
- `SUPERVISOR_AUTHORIZATION_EXPIRE_MINUTES`, `SUPERVISOR_FORM_EXPIRE_MINUTES`,
  `SUPERVISOR_CREATION_EXPIRE_MINUTES`;
- `PASSWORD_RESET_EXPIRE_MINUTES`, `PASSWORD_RESET_TOKEN_MINUTES`,
  `PASSWORD_RESET_IP_LIMIT_PER_HOUR`.

## Supervisor profile (Supervisor token required; migration `0008`)

| Endpoint | Body | Result |
|---|---|---|
| `GET /api/v1/supervisor/profile` | — | Profile; `read_only_fields`: organization_name, organization_id, supervisor_id |
| `POST /api/v1/supervisor/profile/changes` | `{"current_password", "full_name"?, "username"?, "email"?, "new_password"?, "confirm_new_password"?}` | Code sent to the registered email (and a second code to a new email) |
| `POST .../changes/{id}/resend-otp` | — | New code(s) |
| `POST .../changes/{id}/verify` | `{"otp", "new_email_otp"?}` | Changes applied: `{"status": "SAVED", "profile", "signed_out"}` |
| `DELETE .../changes/{id}` | — | Cancelled; nothing changes |

Rules:
- **Read-only fields:** sending a read-only field is refused (`422`). The database also refuses any
  change of a Supervisor ID.
- **Wrong current password:** `403 CURRENT_PASSWORD_INCORRECT`; it counts toward the sign-in lock.
- **No changes:** `422 NO_CHANGES`.
- **Uniqueness:** a taken username is refused at Save; a taken email at verify, and nothing changes.
- **New email:** without the new email's code, the answer is `400 NEW_EMAIL_OTP_REQUIRED` and nothing
  changes.
- **New Save:** replaces an unfinished one.
- **New password:** `signed_out` is `true`, and every earlier token is refused.
- **Isolation:** another Supervisor cannot verify or cancel this change (`404`).

## Forgot password with Supervisor ID

Supervisors can call `POST /api/v1/auth/password-reset` with `{"supervisor_id", "email"}` instead of
`{"organization_id", "email"}`. Send exactly one of the two IDs; otherwise the answer is `422`. The other
steps and all protections are unchanged.
