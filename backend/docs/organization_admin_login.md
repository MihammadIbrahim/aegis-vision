# Organization Admin sign-in and dashboard

An Organization Admin signs in with four values, then an emailed code:
- **Organization Name**, exactly as registered, including capitalization;
- **Organization ID**, for example `ABCDEF12345678901` (older IDs: `ORG-` + 11 digits);
- **Admin email address**, matched ignoring capitalization;
- **password**.

Only after the code is verified is an access token issued.

Sources:
- requirements Section 9.1 (password + email OTP on every login);
- decision K in
  [`requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md`](requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md).

Not covered here: Supervisor and Guard sign-in, and additional-Admin applications and approval
(Stage 4).

## Endpoints

All live under `/api/v1`. Errors return `{"detail": ..., "code": ...}`, plus `Retry-After` for
limits, as in [organization_registration.md](organization_registration.md).

### 1. `POST /auth/organization-admin/login`

```json
{"organization_name": "Demo Logistics", "organization_id": "MSPJKT81250923566",
 "email": "ali@demo-logistics.example.com", "password": "<password>"}
```

`200 OK`. No token is issued yet:

```json
{"login_id": "298fd60c-2904-487c-a00b-4b45090b133b", "status": "OTP_REQUIRED",
 "email": "al***@demo-logistics.example.com", "delivery": "DEV_OUTBOX",
 "otp_expires_in_seconds": 300, "resend_available_in_seconds": 60,
 "login_expires_at": "2026-10-27T10:52:55.093911Z"}
```

Any wrong or mismatched value gives the same `401`:

```json
{"detail": "The organization details, email address or password are incorrect.", "code": "INVALID_CREDENTIALS"}
```

That covers:
- the organization name (even if only the capitalization differs);
- an Organization ID from another organization;
- the email or the password;
- a pending registration;
- an account that is not an Admin or has no password;
- an Admin whose account, campus or organization is not active.

### 2. `POST /auth/organization-admin/login/{login_id}/resend-otp`

Emails a new code; the previous one stops working.

### 3. `POST /auth/organization-admin/login/{login_id}/verify`

Request body: `{"otp": "123456"}`. The response is `200 OK`:

```json
{"access_token": "<JWT>", "token_type": "bearer", "expires_in": 1800}
```

### 4. `GET /organization-admin/dashboard`

Send `Authorization: Bearer <token>`. It takes no IDs: the organization and campus are always
the caller's own.

```json
{"organization": {"organization_id": "MSPJKT81250923566", "name": "Demo Logistics", "status": "ACTIVE",
                  "official_email": "ali@demo-logistics.example.com", "official_phone_number": "+923217654321"},
 "campus": {"name": "Main Campus", "status": "ACTIVE"},
 "admin": {"user_id": "VMEVTG07167403935", "full_name": "Ali Raza", "username": "ali.raza",
           "email": "ali@demo-logistics.example.com", "phone_number": "+923217654321",
           "role": "ADMIN", "is_main_admin": true},
 "summary": {"active_admins": 1, "active_supervisors": 0, "active_guards": 0, "cameras": 0,
             "pending_campus_requests": 0},
 "not_available_yet": ["alerts", "incidents", "recordings", "evidence", "ai_detection", "guard_reports"]}
```

- **What the counts cover:** only the Admin's own campus, read from existing tables.
  `pending_campus_requests` is shown to the Main Admin only (`null` for others).
- **No made-up figures:** modules that are not built yet are listed in `not_available_yet`
  instead of being given numbers.
- **Who is refused:** Supervisors and Guards get `403`. Missing, expired or forged tokens, Super
  Admin tokens, and inactive accounts get `401`. A suspended or inactive organization gets
  `403 ORGANIZATION_INACTIVE`, even with a token issued before.

### Error codes specific to sign-in

| HTTP | `code` | When |
|---|---|---|
| 401 | `INVALID_CREDENTIALS` | Any wrong value (see above); also at verify if the account was deactivated meanwhile |
| 429 | `ACCOUNT_LOCKED` | `ORG_LOGIN_MAX_ATTEMPTS` wrong passwords within `ORG_LOGIN_LOCKOUT_MINUTES`; locked for that long, even with the right password |
| 400 | `OTP_INVALID` / `OTP_EXPIRED` / `OTP_NOT_ISSUED` | Wrong, expired or missing code |
| 429 | `OTP_ATTEMPTS_EXCEEDED` | `OTP_MAX_ATTEMPTS` wrong codes; request a new code |
| 429 | `OTP_RESEND_COOLDOWN` | Resend within `OTP_RESEND_COOLDOWN_SECONDS` |
| 429 | `OTP_HOURLY_LIMIT` | `OTP_HOURLY_LIMIT` codes sent to this account in the last hour |
| 429 | `TOO_MANY_REQUESTS` | `ORG_LOGIN_IP_LIMIT_PER_HOUR` sign-in requests from one client IP |
| 409 | `LOGIN_ALREADY_COMPLETED` | The code was already used (replay) |
| 410 | `LOGIN_EXPIRED` | Not finished within `ORG_LOGIN_EXPIRE_MINUTES` |
| 404 | `LOGIN_NOT_FOUND` | Unknown `login_id` |
| 503 / 502 | `EMAIL_DELIVERY_NOT_CONFIGURED` / `EMAIL_DELIVERY_FAILED` | No code could be sent; nothing is kept and no token is possible |

## Design

- **Tokens.**
  - The token is the existing organization (`tenant` audience) access token. It contains only
    the account ID, audience, type, issue time and expiry (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`,
    30 by default).
  - Role, organization and campus are always read fresh from the database on each request,
    through the existing `get_current_actor` and Row-Level Security.
  - Super Admin tokens (`platform` audience) are refused, and these tokens are refused by Super
    Admin routes. Super Admin sign-in is unchanged.
- **Database access.** Migration `0006_organization_admin_login`:
  - adds `organizations.status` (default `ACTIVE`; nothing changes it yet);
  - adds failed-login fields on `user_credentials`;
  - adds the tables `admin_login_sessions` and `admin_login_otp_challenges`.

  The app role has no rights on these tables or on `user_credentials`. It calls five narrow
  functions:

  | Function | What it does |
  |---|---|
  | `app_admin_login_lookup` | Returns an account only for an exact name + Organization ID + email match |
  | `app_admin_login_record_failure` | Counts a wrong password; locks the account at the limit |
  | `app_admin_login_start` | Clears the failure count and opens a short-lived sign-in |
  | `app_admin_login_issue_otp` | Enforces cooldown and the hourly limit, retires the old code |
  | `app_admin_login_verify_otp` | Checks the code once, re-checks the account is still active, completes the sign-in |

- **Same answer for everything wrong.** Every failure looks alike, and an unknown account still
  costs a full Argon2id check, so timing does not reveal which value was wrong. A locked account
  does say so (`429`): only someone who already knows the exact name, Organization ID and email
  sees that.
- **Dashboard isolation, twice.** Each query filters by the caller's own organization and campus,
  and RLS enforces the same rule in the database. Possessing an Organization ID grants nothing.

## Testing locally

1. In `backend/.env`, set `APP_ENV=development` and `EMAIL_DELIVERY_MODE=dev_outbox`. Codes are
   then written to files instead of emailed; never use this on a server.
2. Start the API from `backend/services/core_api`:
   `uvicorn src.main:create_app --factory`.
3. Register an organization; see [organization_registration.md](organization_registration.md).
   Note its Organization ID.
4. Call the sign-in endpoint and read the newest code:
   `ls -t .dev_outbox | head -1 | xargs -I{} cat .dev_outbox/{}`.
5. Verify the code, then call `GET /api/v1/organization-admin/dashboard` with the token. The
   Swagger UI at `/docs` works too.

For real email, set `EMAIL_DELIVERY_MODE=brevo` with `BREVO_API_KEY` and `BREVO_SENDER_EMAIL`.

## Limitations

- **No refresh tokens or logout yet.** The project has none for any role. A token stays valid
  until it expires (30 minutes), unless the account becomes inactive (checked on every request)
  or the organization is suspended (checked on the Admin routes).
- **No early sign-out after credential changes:** there is no password change or reset for
  Organization Admins yet, so there is no token version for them either.
- **Shared code limit:** `OTP_HOURLY_LIMIT` (5) also limits sign-ins: a sixth sign-in within one
  hour is refused with `OTP_HOURLY_LIMIT`.
- **Per-IP limit:** kept per API process (see the registration doc).
- **`ADMIN_APPROVAL_PENDING` / `ADMIN_REQUEST_REJECTED`:** no pending additional Admins exist
  until Stage 4.
- **Sign-in audit log:** not implemented yet.
