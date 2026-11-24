# Organization web app (Admins and Supervisors)

React + Vite + TypeScript (the approved stack, requirements Section 2). So far it covers
**Alerts, Manual Reports and Notifications** (decision D-013) and **Personnel Management, the
Excel/CSV personnel import and restricted-area access** (decision D-016,
`backend/docs/personnel_management.md`). Other dashboard pages are not built yet.

## Run

```bash
npm install
VITE_API_TARGET=http://127.0.0.1:8000 npm run dev   # the Core API must be running there
```

The dev server forwards `/api` to the Core API, so there is no CORS setup. Sign in with the
organization name, the organization ID, your email and password, then the code sent to your email.
The proxy also forwards WebSocket upgrades (`ws: true`), so live updates work in development; a production
reverse proxy must forward `Upgrade` for `/api/v1/realtime/ws` too.

## Real-time updates and voice calls (decision D-026)

- **Live pages:** one WebSocket per tab (`src/realtime/`, the Guard app's protocol: token in the first message,
  back-off reconnect 1–30 s, one re-read after reconnecting). Pages call `useLiveRefresh(reload, match, {debounceMs,
  fallbackMs})` and poll while the socket is down. The topbar shows **Live**, **Connecting… / Reconnecting…** or
  **Polling**.
- **Voice calls** (`src/features/calls/`): Start / Join on alert and case pages, an incoming-call banner on every page,
  and a call dock. `livekit-client` is loaded only when a call connects (its own chunk). The API must run with
  `VOICE_CALL_PROVIDER=livekit` and LiveKit keys; otherwise the pages say calls are not configured and the chat still
  works. Browsers allow the microphone only on https or localhost.

## AI Investigation chatbot (decision D-027)

`/assistant` (Admins and Supervisors; "AI Investigation" in the navigation) and "Investigate with AI" on Incident
Detail (`src/features/assistant/`). Read-only and without uploads: the Core API answers from this campus's records
under Row-Level Security (`/api/v1/assistant/*`); the page shows grounded text, tables, bars and timelines, clickable
references, the tools and queries used, a missing-data banner and an honest provider badge (`AI_LLM_PROVIDER`).

## Check

```bash
npm test          # vitest (jsdom)
npm run build     # type check + production build into dist/
```

## Roles

| Page | Organization Admin | Supervisor |
|---|---|---|
| Dashboard (start page) | organization and campus, open-alert figures, reports awaiting review, unread notifications, staff counts, active personnel, pending campus requests (Main Admin), locations, cameras, Guards, restricted areas, recent activity | the same operations, site and restricted-area figures; no Admin, Supervisor or personnel counts |
| Alerts | own campus: filters, details, recipients, history, chat; resolve, dismiss (reason), escalate within the campus | the same |
| Manual Reports | own campus: list, details, photo, mark as reviewed | the same |
| Notifications | own notifications only; mark read | the same |
| Personnel Directory | search, filters, contact details, add, import | read-only, basic columns |
| Person details | full record, edit, deactivate/reactivate, photo, history | basic details, current access |
| Bulk Import, Import History, Categories & Fields | yes | not shown |
| Restricted-Area Access | mark areas, grant / change / revoke, history, access check | who is authorized, access check |

Hiding a button is only for convenience: the Core API and the database enforce every rule. The
access token is kept in `sessionStorage` and removed on sign-out or when the tab closes.

## Design system (Phases 1–2)

- **Tokens:** `src/styles/tokens.css`, with light values in `:root` and dark values in
  `[data-theme="dark"]`. Navy, slate and teal, with the security-status palette. Components use the
  variables, never raw colours.
- **Contrast:** `src/test/contrast.test.ts` checks every foreground/background pair in use, in both
  themes, against WCAG AA (4.5:1 for text, 3:1 for control borders and focus rings). It fails if a
  token change breaks a pair.
- **Theme:** the system setting until the user chooses; the choice is kept in `localStorage`
  (`src/lib/theme.ts`), and `index.html` applies it before the first paint.
- **Fonts:** IBM Plex Sans and Mono, self-hosted through `@fontsource` (no CDN).
- **Statuses:** one map, `src/lib/status.ts`: tone, label and `lucide-react` icon for every status
  value the API returns. Status is never shown by colour alone.
- **Shared components** (`src/components/ui/`): Button, Field, Dialog, ConfirmDialog (with
  `useConfirm`), Toast, DataTable, Pagination, PageHeader, Empty/Loading/Error states and
  StatusBadge. Also `hooks/useAsync` and `lib/format`. They never import feature code. The
  Personnel pages switch over to them in Phase 4.

## Tests

- `src/test/characterization/`: pins the current Personnel pages' behaviour before refactoring:
  request URLs, methods and bodies, role-based visibility, and Admin-only routes.
- `src/components/ui/ui.test.tsx`: keyboard, focus and label behaviour of the shared components.
- `src/test/phase5.test.tsx`: dashboard figures (metrics), each role's endpoints, loading, error and
  empty states, and that no request carries an organization or campus ID.
- `src/test/phase6.test.tsx`: alerts, manual reports and notifications: request URLs and bodies,
  dialogs, closed alerts, pagination without a total, and role visibility.
- `src/test/d027.test.tsx`: the AI Investigation page (starter prompts, multi-turn chat, references, tools drawer,
  missing data, case context, deleting a chat, unreachable model, roles) and "Investigate with AI".
- `src/test/d026.test.tsx`: the WebSocket client, live pages and chats, the connection indicator, sign-out on
  `ORGANIZATION_INACTIVE`, ringing, the call dock and the honest call notices (fake socket and voice client from
  `src/test/realtimeFakes.ts`; no test opens a real socket).
- `smoke/`: live checks through the dev proxy against a real API on a scratch database (see
  `smoke/README.md`), including `realtime_comms_smoke.py` (Guards, an Admin and a Supervisor on WebSockets at once) and
  `chatbot_smoke.py` (the chatbot's answers against independent REST counts).

## Structure (Phase 4)

```text
src/
  app/          App, providers, AppShell / AuthLayout, the combined route list
  api/client.ts shared fetch wrapper (ApiError, downloads); api/types.ts: Role, Session
  auth/         sign-in page and API, AuthContext, AdminOnly, permissions (nav groups)
  components/ui shared building blocks (never import feature code)
  features/<name>/  api.ts, types.ts, routes.tsx, index.ts (public surface), pages/, components/
  realtime/     WebSocket client, RealtimeProvider, useLiveRefresh, ConnectionStatus (D-026)
  hooks/ lib/ styles/ test/
```

Features import other features only through their `index.ts`. A new module adds one folder and
one line in `app/routes.tsx`.
