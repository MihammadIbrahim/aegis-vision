# First Organization Admin registration (email OTP)

A new organization registers with these fields (all required):
- Organization Name
- Campus Name: the name of its first campus (decision D-021, 2026-11-13; before that the first campus was
  always called "Main Campus" and the form had no campus field)
- Admin Full Name
- Username
- Email Address
- Phone Number
- Password

The applicant then enters the 6-digit code emailed to them. Only after the code is verified
does anything real exist. Verification creates, in one transaction:
- the **organization**, with its permanent **Organization ID**: 6 capital letters + 11 digits,
  for example `ABCDEF12345678901`. IDs issued before migration `0005` (`ORG-` + 11 digits) stay
  valid;
- its first campus, named as entered (2–100 characters, letters or digits, spaces collapsed);
- the **Main Admin** account, with a user ID of 6 capital letters + 11 digits.

The completion answer returns both the **Organization ID** and the **Main Admin ID**; the web app shows them with
copy buttons. Admins of additional campuses need the exact organization name and the Organization ID to request
their campus ("Already Registered Organization?", decision D-020, `POST /auth/campus-requests`; see
`api_contracts.md`).

Registration never signs anyone in. Organization Admin login (Organization Name +
Organization ID + email + password + email OTP) is a separate, later step.

Sources:
- requirements Section 8;
- decisions A–F in
  [`requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md`](requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md).

## Endpoints

All three are public (no token) and live under `/api/v1/auth/organization-registrations`.
Errors return `{"detail": "<message>", "code": "<STABLE_CODE>"}`. Limit errors also include
`"retry_after_seconds"` and a `Retry-After` header.

### 1. Submit: `POST /api/v1/auth/organization-registrations`

```json
{
  "organization_name": "Lakeside Institute",
  "campus_name": "City Campus",
  "admin_full_name": "Maryam Siddiqui",
  "username": "maryam.s",
  "email": "maryam@lakeside.example.com",
  "phone_number": "+92 300 123-4567",
  "password": "<12-128 characters, a letter and a digit>"
}
```

`201 Created`:

```json
{
  "registration_id": "da72cc74-66ee-4c32-bf1e-ead83d5eb0ca",
  "status": "PENDING_EMAIL_VERIFICATION",
  "email": "ma***@lakeside.example.com",
  "delivery": "EMAIL",
  "otp_expires_in_seconds": 300,
  "resend_available_in_seconds": 60,
  "registration_expires_at": "2026-10-27T10:08:03.547920Z"
}
```

`delivery` is `EMAIL` when a real provider accepted the message, or `DEV_OUTBOX` in local
development (the email was written to a file, not sent).

### 2. Resend: `POST /api/v1/auth/organization-registrations/{registration_id}/resend-otp`

- **Body:** none.
- **Response:** the same as submit, with `200 OK`.
- **Effect:** the previous code stops working.

### 3. Verify: `POST /api/v1/auth/organization-registrations/{registration_id}/verify`

Request body: `{"otp": "193203"}`. The response is `200 OK`:

```json
{
  "status": "COMPLETED",
  "organization_id": "MAJYUE14753916112",
  "organization_name": "Lakeside Institute",
  "official_email": "maryam@lakeside.example.com",
  "official_phone_number": "+923001234567",
  "campus_name": "City Campus",
  "main_admin": {
    "user_id": "WIMDNW70679276498",
    "full_name": "Maryam Siddiqui",
    "username": "maryam.s",
    "email": "maryam@lakeside.example.com",
    "phone_number": "+923001234567",
    "role": "ADMIN",
    "is_main_admin": true
  },
  "registered_at": "2026-10-27T09:08:03.665971Z"
}
```

`organization_id` here is the Organization ID shown to people. The database primary keys of
the organization, campus and user are never returned.

### Error codes

| HTTP | `code` | When |
|---|---|---|
| 422 | (FastAPI list of field errors) | A field is missing or of the wrong type, the code is not 6 digits, or an unknown field such as `organization_id` was sent |
| 422 | `INVALID_ORGANIZATION_NAME`, `INVALID_FULL_NAME`, `INVALID_USERNAME`, `INVALID_EMAIL`, `INVALID_PHONE_NUMBER`, `WEAK_PASSWORD` | A field breaks its rule (see below) |
| 409 | `ORGANIZATION_NAME_TAKEN` | The name exists, ignoring capitalization: at submit, or at verify if another registration completed first |
| 409 | `USERNAME_TAKEN` | The username exists, ignoring capitalization |
| 409 | `EMAIL_ALREADY_REGISTERED` | At verify only: the email already belongs to an account |
| 400 | `OTP_INVALID` | Wrong code; the message says how many attempts are left |
| 400 | `OTP_EXPIRED` | The code is older than `OTP_EXPIRE_SECONDS` |
| 400 | `OTP_NOT_ISSUED` | No usable code; request a new one |
| 429 | `OTP_ATTEMPTS_EXCEEDED` | `OTP_MAX_ATTEMPTS` wrong codes; even the right code is refused until a new one is sent |
| 429 | `OTP_RESEND_COOLDOWN` | Resend within `OTP_RESEND_COOLDOWN_SECONDS` of the last code |
| 429 | `OTP_HOURLY_LIMIT` | `OTP_HOURLY_LIMIT` codes already sent to this email address in the last hour, across all registrations |
| 429 | `TOO_MANY_REQUESTS` | Too many registration requests from one client IP |
| 409 | `REGISTRATION_ALREADY_COMPLETED` | The registration is complete; its code cannot be used again |
| 410 | `REGISTRATION_EXPIRED` | Not verified within `ORG_REGISTRATION_EXPIRE_MINUTES`; the applicant's details have been erased |
| 404 | `REGISTRATION_NOT_FOUND` | Unknown `registration_id` |
| 503 | `EMAIL_DELIVERY_NOT_CONFIGURED` | No email provider is configured, so no code can be sent; nothing is stored |
| 502 | `EMAIL_DELIVERY_FAILED` | The provider refused or could not be reached; nothing is stored |

**Why a taken email is reported only at verify.** If submit said "email already registered",
anyone could test which emails have accounts. The answer is given only to the person who
received the code, which proves they own the address.

### Field rules

- **Organization name:**
  - 2–150 characters, containing letters or digits;
  - single spaces only, with no leading or trailing space;
  - no invisible or control characters.

  The name is stored **exactly as entered**. A badly spaced name is refused with an
  explanation, never silently "fixed". Names must be unique ignoring capitalization.
- **Full name:** 2–120 characters, containing letters. Surrounding spaces are removed.
- **Username:**
  - 3–64 characters: letters, digits, `.`, `_` or `-`;
  - unique ignoring capitalization.
- **Email:**
  - a valid address;
  - reserved domains such as `.test` and `localhost` are refused;
  - the domain part is stored in lower case.
- **Phone number:**
  - required, but not verified by SMS;
  - 7–15 digits with an optional leading `+`;
  - spaces, dashes, dots and brackets are removed, so `+92 300 123-4567` is stored as
    `+923001234567`.
- **Password:**
  - 12–128 characters, with a letter and a digit;
  - must not contain the username or equal the email;
  - stored only as an Argon2id hash.

## Email delivery

Emails sent by this step contain only fixed wording and the code. Nothing typed into the form
is included, so the form cannot be used to send arbitrary text to someone else's inbox.

Set `EMAIL_DELIVERY_MODE` in `backend/.env`:

| Mode | What happens |
|---|---|
| `none` (default) | No provider. Submit and resend answer `503 EMAIL_DELIVERY_NOT_CONFIGURED`; nothing is stored, and nothing claims to have been sent |
| `dev_outbox` | Each email is written to a file in `DEV_OUTBOX_DIR` instead of being sent. Refused at startup unless `APP_ENV=development` |
| `brevo` | Real email through Brevo's HTTP API |

### Reading development emails (`dev_outbox`)

With `APP_ENV=development` and `EMAIL_DELIVERY_MODE=dev_outbox`, every email becomes one text
file in `DEV_OUTBOX_DIR`. The default, `.dev_outbox`, is relative to the folder you start the API
from, so usually `backend/services/core_api/.dev_outbox/`. To read the newest one:

```bash
ls -t .dev_outbox/ | head -1 | xargs -I{} cat .dev_outbox/{}
```

The files contain codes:
- the folder is created readable by your user only;
- it is git-ignored;
- codes are never written to the application log.

### Connecting Brevo

1. In Brevo, add and verify a sender address or domain.
2. Create an API key (Brevo dashboard → SMTP & API → API keys).
3. In your own `backend/.env` (never in a committed file), set:
   - `EMAIL_DELIVERY_MODE=brevo`
   - `BREVO_API_KEY=<your key>`
   - `BREVO_SENDER_EMAIL=<the verified sender>`
   - optionally `BREVO_SENDER_NAME`
4. Restart the API.

Startup refuses `brevo` mode without the key or the sender, naming the missing variable but
never printing values.

What the API does with Brevo:
- It calls `POST https://api.brevo.com/v3/smtp/email` with the `api-key` header.
- A 2xx answer means Brevo **accepted** the message for delivery. Delivery to the inbox itself is
  not confirmed.
- Any other answer, or a timeout (`EMAIL_SEND_TIMEOUT_SECONDS`), returns
  `502 EMAIL_DELIVERY_FAILED`, and the registration is not stored.

To use another provider, add a class with the same `is_configured`, `delivery_channel` and
`send()` members as `BrevoEmailSender`
(`src/application/interfaces/i_email_service.py`), and choose it in
`src/infrastructure/email/factory.py`.

## Configuration

All variables are optional with safe defaults; see `backend/.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `production` | `development` allows the dev outbox |
| `EMAIL_DELIVERY_MODE` | `none` | `none`, `dev_outbox` or `brevo` |
| `DEV_OUTBOX_DIR` | `.dev_outbox` | Where development emails are written |
| `BREVO_API_KEY`, `BREVO_SENDER_EMAIL` | (blank) | Required for `brevo` |
| `BREVO_SENDER_NAME` | `AI CCTV Security` | Sender display name |
| `BREVO_API_URL` | `https://api.brevo.com/v3/smtp/email` | Must be `https://` |
| `EMAIL_SEND_TIMEOUT_SECONDS` | `10` | Provider timeout |
| `OTP_SECRET_KEY` | (blank) | Key for the stored code hashes, at least 32 characters. Blank means it is derived from `JWT_SECRET_KEY`. Changing it invalidates codes already sent |
| `OTP_EXPIRE_SECONDS` | `300` | Code lifetime |
| `OTP_RESEND_COOLDOWN_SECONDS` | `60` | Wait between codes for one registration |
| `OTP_MAX_ATTEMPTS` | `5` | Wrong codes allowed per code |
| `OTP_HOURLY_LIMIT` | `5` | Codes per email address per hour |
| `ORG_REGISTRATION_EXPIRE_MINUTES` | `60` | Unverified registrations are erased after this |
| `ORG_REGISTRATION_IP_LIMIT_PER_HOUR` | `30` | Registration requests per client IP per hour |

## Data model (migration `0004_organization_registration`)

- **`organizations`:**
  - `organization_code`: the Organization ID. Unique, format-checked, and permanent (a trigger
    refuses changes). Organizations created before `0004` received a random code.
  - Migration `0005` changed the format of **new** IDs to 6 letters + 11 digits. The check now
    accepts that format and the earlier `ORG-` + 11 digits; no existing ID was rewritten.
  - `official_email` and `official_phone`: the organization's contacts, stored apart from the
    Admin's login email and phone.
  - New unique index on `lower(name)`.
- **`users`:**
  - New `username` (unique ignoring capitalization) and `phone` columns.
  - The display-ID rule now also accepts 6 capital letters + 11 digits for `ADMIN` and
    `SUPERVISOR`. Existing `ADM-`/`SUP-`/`GRD-` IDs stay valid.
- **`user_credentials`:** password hashes, kept apart from `users` so that no tenant query can
  read them.
- **`organization_registrations`:** one row per submitted form.
  - The applicant's details, including the password hash, exist only while it is `PENDING`.
    They are erased when it becomes `COMPLETED` (copied into the new account) or `EXPIRED`.
  - The reserved Organization ID stays in the row, so it is never handed out again.
- **`email_otp_challenges`:** codes, stored only as HMAC-SHA-256 hashes keyed with the server
  secret.
  - At most one usable code per registration; issuing a new one retires the old.
  - Rows older than a day are deleted. The check runs whenever a registration is submitted;
    expired pending registrations are erased at the same moment.

**Database access.** The app database role has **no** rights on the three new tables. Before
anyone is signed in, Row-Level Security cannot identify a caller, so the API uses four narrow
`SECURITY DEFINER` functions:

| Function | What it does |
|---|---|
| `app_org_registration_submit` | Checks the name and username, reserves the Organization ID, stores the pending row |
| `app_org_registration_issue_otp` | Enforces the cooldown and hourly limit (locked per address), retires the old code, stores the new hash |
| `app_org_registration_verify_otp` | Counts wrong attempts; a right code is used up and marks the email verified |
| `app_org_registration_complete` | Requires a verified email in the same transaction, then inserts the organization, its first campus (the name given, migration 0022), the Main Admin and password hash, all or nothing |

The functions lock the registration row, so concurrent requests cannot create two
organizations, campuses or Main Admins. Unique indexes on the organization name, email,
username and IDs back this up. The Super Admin's database role cannot call any of these
functions.

## Security notes

- **No secrets in responses or logs:** the password, its hash and the code never appear in
  responses or logs.
  - 422 responses do not echo submitted values.
  - Database errors are logged without query values (`hide_parameters`).
- **Atomic completion:** a failure during completion rolls back everything, including the use of
  the code. Retrying with the same code then creates the organization with the same,
  already-reserved Organization ID.
- **Per-IP limit:** kept in the API process's memory, so it is per replica and resets on restart.
  The per-address and per-code limits are in PostgreSQL and always hold. The client IP is the
  direct connection's address; `X-Forwarded-For` is not trusted.

## Not in this step

- **Organization Admin login and login OTP:** Stage 3.
- **"Already Registered Organization?" applications and Main Admin approval:** Stage 4.
  - Approval needs password + fresh OTP; rejection and 72-hour cleanup; `ADMIN_APPROVAL_PENDING`
    and `ADMIN_REQUEST_REJECTED`.
  - Admins created by campus approval still get `ADM-` IDs until then.
- **Not built yet:**
  - editing organization contact details;
  - audit-log entries for registrations;
  - a way to look up a forgotten Organization ID.
