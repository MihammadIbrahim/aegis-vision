# Platform Super Admin web app (`super_admin/`)

A separate React + Vite + TypeScript application for the **Platform Super Admin** (decision D-019). It
shares no code at runtime with the organization web app (`website/`): it has its own build, its own
copies of the design system (tokens, base and component styles, UI components) and its own session.

## Run

```bash
npm ci
VITE_API_TARGET=http://127.0.0.1:8000 npm run dev   # /api is proxied to the Core API
npm test            # Vitest
npm run typecheck   # tsc
npm run build
```

## Rules

- **Sign-in:** username + password only (`POST /api/v1/platform/auth/login`). No email code, no sign-up,
  no self-service password reset. Forgotten credentials are restored with the emergency recovery
  credentials from the server `.env`, signing in on the same form; the app then shows the recovery panel.
- **Session isolation:** the platform token is kept in `sessionStorage` under `cctv.platform.accessToken`
  (the organization app uses `cctv.accessToken`); the theme under `cctv.platform.theme`. A `401` signs out.
- **Token rotation:** username, password and recovery changes return a fresh token, which replaces the
  stored one (older Super Admin sessions stop working on the server).
- **Sensitive actions** (Main Admin contact, organization status, AI settings) ask for the Super Admin's
  current password in a dialog.
- **Contract:** `src/api/types.ts` and `src/api/platform.ts` follow the SRS section of
  `FYP_Documentation/FYP_Final_Super_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt` field for field.
- **No organization media or records:** no page shows footage, stream addresses, alerts, chat, reports or
  personnel; the API offers none.

## Pages

| Route | Page |
|---|---|
| `/` (signed out) | Sign-in |
| `/dashboard` | Platform overview |
| `/organizations` | Organization management (+ deleted organization names) |
| `/organizations/:organizationId` | Organization details, Main Admin edit, status control |
| `/ai-settings` | Platform AI availability and defaults |
| `/health` | System health |
| `/audit-logs` | Platform audit log |
| `/profile` | Profile, username, password, emergency recovery |

## Live smoke check

`smoke/platform_smoke.py` calls every route the app uses through the dev proxy and compares each response's
fields with `src/api/types.ts`. Run it only against a scratch database:

```bash
python3 smoke/platform_smoke.py --web http://127.0.0.1:5182 --username U --password P \
  --recovery-username RU --recovery-password RP
```
