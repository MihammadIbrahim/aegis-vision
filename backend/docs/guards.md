# Guards: creation, sign-in, forgot password, profile

Decisions T–X in [`requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md`](requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md).
Migration `0009_guards`. Errors use `{"detail", "code"}`.

## Guard creation (Admin token; Supervisors could until D-017)

The steps are the same as Supervisor creation (see [supervisors_and_password_reset.md](supervisors_and_password_reset.md)),
under `/api/v1/guards`:

| Step | Endpoint | Body | Result |
|---|---|---|---|
| 1 | `POST /api/v1/guards/authorization` | `{"current_password"}` | Code emailed to the acting user |
| | `.../authorization/{id}/resend-otp` | — | New code |
| 2 | `.../authorization/{id}/verify` | `{"otp"}` | The form may be submitted once |
| 3 | `POST /api/v1/guards` | `{"authorization_id", "full_name", "username", "email", "phone_number", "password"}` | `201`; code emailed to the Guard |
| | `.../creations/{id}/resend-otp` | — | New code |
| 4 | `.../creations/{id}/verify` | `{"otp"}` | `201` with `guard_id` (27 characters) and the profile |

Rules:
- **Grants are separate:** confirmations for adding Guards and for adding Supervisors cannot be used for
  each other.
- **Where the Guard goes:** the actor's own organization and campus.
- **Who:** Guards, Super Admin tokens and other organizations are refused.
- **Settings:** the Supervisor-creation timing settings (`SUPERVISOR_*_EXPIRE_MINUTES`) apply.

## Guard app

| Endpoint | Body | Result |
|---|---|---|
| `POST /api/v1/auth/guard/login` | `{"operational_mode", "guard_id", "email", "password"}` | Code emailed; no token yet. A mode not matching the assignment: `409 OPERATIONAL_MODE_MISMATCH` (after the right password) |
| `.../login/{login_id}/resend-otp` | — | New code |
| `.../login/{login_id}/verify` | `{"otp", "operational_mode"}` | Mode checked again; `{access_token, token_type, expires_in, operational_mode, dashboard}` |
| `GET /api/v1/guard/dashboard` | — | Guards only; includes `operational_mode` and `dashboard` |
| `POST /api/v1/auth/password-reset` | `{"guard_id", "email", "operational_mode"}` | Forgot password (then `/verify` and `/complete`, as for staff); a mode mismatch gets the same answer and no code |
| `GET /api/v1/guard/profile` | — | Read-only: name, username, Guard ID, organization |
| `POST /api/v1/guard/profile/changes` | `{"current_password", "email"?, "phone_number"?, "new_password"?, "confirm_new_password"?}` | Code(s) emailed |
| `.../changes/{id}/verify` / `resend-otp` / `DELETE` | | As for the Supervisor profile |

Operational mode (decision D-018): `STANDBY` (no location; Standby Emergency Dashboard, `STANDBY_EMERGENCY`) or
`STATIONED` (Stationed / Standalone: a building, floor or area; Stationed Monitoring Dashboard,
`STATIONED_MONITORING`). Any change to the Guard's assignment ends the Guard's sessions: the next request gets
`401 ASSIGNMENT_CHANGED` and the app signs in again.

Sign-in rules:
- **Same answer for every wrong value:** a wrong value, an inactive or removed Guard, or an Admin's
  details all give `401 INVALID_CREDENTIALS`.
- **Separate sign-in pages:** Guards cannot use the Organization Admin sign-in page, and staff cannot use
  the Guard one.
- **Shared protections:** locks, code limits and the session cut-off after a password change are the
  same as for staff.

## Guard mobile app and real-time communication (decision D-025, migration 0030)

The app is built in `mobile/` (see `mobile/README.md`). Contract: the SRS section at the end of
`FYP_Documentation/FYP_Final_Guards_Mobile_App_Pages_and_Their_Each_Page_Features.txt`.

| Endpoint | Body | Result |
|---|---|---|
| `GET /api/v1/alerts/{id}/responders` | — | Guards who acknowledged or are responding (name, state, assessment, times, `is_me`); no Guard IDs |
| `GET /api/v1/guard/incidents`, `GET/POST /api/v1/incidents/{id}/messages` | `{"body"}` | Case chats this Guard takes part in |
| `POST /api/v1/alerts/{id}/calls`, `/incidents/{id}/calls`, `/calls/{id}/join\|decline\|leave\|end` | — | Voice calls (LiveKit room tokens, audio only); `503 VOICE_CALL_NOT_CONFIGURED` without keys |
| `POST /api/v1/guard/device-tokens`, `DELETE …/{token}` | `{"token", "platform", "provider", "app_version"?}` | Push token of this phone (moved from any earlier owner; deleted when the account leaves) |
| `WS /api/v1/realtime/ws` | `{"type": "auth", "access_token"}` first | Signals; `session_ended` `ASSIGNMENT_CHANGED` ends the socket at once |

Rules added by D-025:
- **The alarm** rings while the Guard's own recipient state is `NOTIFIED` on an open alert, and stops only after the
  server confirms acknowledge / responding, the alert closes, or the session ends.
- **Forced sign-out** now also reaches an open WebSocket immediately (it was "on the next request" in D-018).
- **`GET /guard/profile`** adds `campus_name`, `status` and `operational_mode`.

Web ↔ Mobile parity (decision D-026, migration 0031): the Admin & Supervisor web app now uses the same WebSocket and
call routes, so a Guard's acknowledgement, assessment, "I'm Responding", alert-chat and case-chat messages appear on the
staff's pages at once, staff replies reach the app at once, and calls ring both ways (a Guard can call the web staff;
staff can call the Guards and end a call for everyone). For the app this added only optional fields: calls carry
`target_title` and `target_location` (shown on the incoming-call card and the call overlay), and `incident.updated`
now arrives for every case history entry with a `kind`. Verified live by `website/smoke/realtime_comms_smoke.py`.
