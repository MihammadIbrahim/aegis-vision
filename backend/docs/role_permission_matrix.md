# Role and permission matrix

Status checked against the code on 2026-11-19 (after D-027).
- **Built rows** are enforced by the API **and** by the database (Row-Level Security / narrow
  functions), and are covered by integration tests.
- **Planned rows** are the requirements' intent, not implemented.

Every organization user works only inside their **own campus**. The Main Admin gets no access to
other campuses' data (decision D-001, D-010). The Super Admin uses a separate database role that
cannot read organization data.

## Built

| Action | Super Admin | Main Admin | Additional Admin | Supervisor | Guard |
|---|---|---|---|---|---|
| Sign in (own method; OTP for organization users) | yes | yes | yes | yes | yes |
| Forgot password (self-service) | — | yes | yes | yes | yes |
| Own profile changes (D-024 for Admins) | username, password | name, username, email, phone, password (password + emailed code) | same as Main Admin | name, username, email, password | email, phone, password |
| Create Supervisors | — | yes | yes | no | no |
| Create Guards | — | yes | yes | **no** (since D-017, migration 0018) | no |
| Staff list and details: Guards (D-017) | — | yes | yes | yes (Guards only) | no |
| Staff list and details: Supervisors | — | yes | yes | no | no |
| Guard details (name, username, phone), set a Guard's password | — | yes (own password) | yes (own password) | yes (own password; Admins notified) | no |
| Deactivate / reactivate Guards and Supervisors (status LEFT / ACTIVE) | — | yes (own password) | yes (own password) | no | no |
| Campus requests: list, history, approve (password + emailed code), reject (password; erases the applicant's details) | **no** (`403`) | yes | no (`403`) | no | no |
| Ownership transfer (start) | — | yes | no | no | no |
| Accept/reject a transfer offered to them | — | — | yes | no | no |
| Ownership history | — | yes (while Main Admin) | no | no | no |
| Delete own account (Admin ID + password + OTP) | — | after transfer, or if sole Admin | yes | no | no |
| Buildings / outdoor perimeters: create, edit, delete | — | yes | yes | **no** (view) | no |
| Floors, areas (incl. boundaries): create, edit, delete | — | yes | yes | yes | no |
| Guard location: set, remove (Standby ↔ Stationed / Standalone; ends the Guard's sessions, D-018) | — | yes | yes | yes (Admins notified) | own, read-only |
| Cameras: list, view, add, edit, delete, set location | — | yes | yes | yes | no |
| **D-024:** any organization route with a Super Admin token | `403 SUPER_ADMIN_NOT_ALLOWED` | — | — | — | — |
| Audit logs: view, filter, export CSV | **no** (no route; no database privilege) | every record of own campus | every record of own campus | operational categories + own records | no |
| Incidents: list, view, open, edit, link alerts / reports / evidence, assign, notes, status | no | own campus | own campus | own campus | no |
| Case chat | no | own campus | own campus | own campus | invited, or a recipient of a linked alert |
| Alerts: set the handler; view escalations, snapshot, surrounding clip | no | yes | yes | yes | no |
| Alert defaults: automatic escalation times, assistance broadcast | no | change (own password) | view | no | — |
| Organization settings: view / change | no | view, change (own password) | view | display preferences only | display preferences only |
| AI configuration: view; campus modules; camera modules | platform availability only (D-019) | view; campus; camera (own password) | view; campus; camera (own password) | view; **camera only** (own password) | no |
| **D-028:** block / unblock a core AI module for one organization (password, audited) | yes | no | no | no | no |
| **D-028:** Supervisor AI feature visibility per module (own password) | no | yes | yes | no | no |
| **D-028:** AI features with a camera (add / edit) and bulk assignment (own password for bulk) | no | yes | yes | yes — only modules visible to Supervisors; hidden modules kept as they are | no |
| Live detection overlay | no | yes | yes | yes | no |
| Person search: search, review, link verified results, save as evidence | **no** | own campus | own campus | own campus | no |
| Evidence tags, case link, location filter | no | yes | yes | yes | no |
| Reports & analytics, dashboard feed, CSV / PDF export | no | own campus | own campus | own campus | no |
| User records (current and former staff), CSV | no | yes | yes | no | no |
| Rejoin a former Supervisor or Guard (same account) | no | yes (own password) | yes (own password) | no | no |
| Permanently delete a former user's personal data | no | yes (password + DELETE) | yes (password + DELETE) | no | no |
| Regenerate own Admin ID | no | yes (own password) | yes (own password) | — | — |
| Regenerate a Guard ID | no | yes (own password) | yes (own password) | yes (own password; Admins notified) | no |
| Regenerate a Supervisor ID | no | **no** (permanent) | **no** | **no** | — |
| Deleted-organization names | yes | no | no | no | no |
| Platform overview, organization list/details/export (metadata and counts only; D-019) | yes | no | no | no | no |
| Edit an organization's Main Admin contact (name, email, phone) | yes (own password) | no | no | no | no |
| Set an organization Active / Inactive / Suspended | yes (own password) | no | no | no | no |
| Platform AI settings: view / change | yes / yes (own password) | no | no | no | no |
| Platform health; platform audit log: view, filter, export | yes | no | no | no | no |
| Organization footage, stream addresses, alerts, chat, reports, personnel | **no** (no route; the platform role cannot read them) | own campus | own campus | own campus (personnel: basic) | alerts sent to them |
| Alerts: view (D-013) | — | own campus | own campus | own campus | only alerts sent to them |
| Alerts: acknowledge, responding, assessment | — | no | no | no | yes, if a recipient |
| Alerts: resolve, dismiss (reason), escalate within the campus | — | yes | yes | yes | no |
| Alert chat: read and post | — | own campus | own campus | own campus | alerts sent to them |
| Manual reports: submit (optional photo), list own | — | no | no | no | yes |
| Manual reports: list, view, photo, review | — | own campus | own campus | own campus | own only (list) |
| Own in-app notifications | — | yes | yes | yes | yes |
| Offline sync (`/guard/sync`) | — | no | no | no | yes |
| Personnel directory: list, search, filter (D-016) | no | yes, with contact details | yes, with contact details | basic columns only | no |
| Personnel details | no | full record | full record | basic details only | no |
| **Register personnel (form)** | no | **yes** | **yes** | **no** | no |
| **Excel/CSV personnel import** (template, upload, mapping, confirm, rollback, history) | no | **yes** | **yes** | **no** | no |
| Personnel: edit, deactivate, reactivate, history | no | yes | yes | no | no |
| Personnel photo: upload, view, remove | no | yes | yes | no (only in an authorized identity review, not built) | no |
| Personnel categories: view / change; custom fields | no | view, change | view, change | view only | no |
| Restricted areas: view / mark or clear | no | view, mark | view, mark | view only (no area `access_note`) | no |
| Restricted-area access: current permissions, access check | no | yes | yes | yes (no notes) | no |
| Restricted-area access: grant, change, revoke, history | no | yes | yes | no | no |
| Live cameras: grid, status, newest frame, fullscreen (D-023) | **no** (no route; no database privilege) | own campus | own campus | own campus | no |
| Camera connection test | no | yes | yes | yes | no |
| Recordings: timeline, AI markers, playback | **no** | own campus | own campus | own campus | no |
| Recording retention (minutes / hours / days) | no | view, change (own password) | view, change (own password) | view, change (own password) | no |
| Evidence: list, view, save from an alert or playback, edit details | **no** | own campus | own campus | own campus | no |
| **Evidence: delete** | no | **yes (own password)** | **yes (own password)** | **no** (provisional: no Supervisor authorization exists yet, D-023 item 8) | no |
| Camera offline / back online notifications | — | yes | yes | yes | no |
| **D-025:** real-time WebSocket (signals for what the user may already see) | **no** (`4403`) | yes | yes | yes | yes |
| Alert responder roster (names, states; no Guard IDs) | no | own campus | own campus | own campus | alerts sent to them |
| Voice call about an alert: start / join / leave | no | own campus | own campus | own campus | alerts sent to them |
| Voice call about a case: start / join / leave | no | own campus | own campus | own campus | invited, or a recipient of a linked alert |
| Voice call: end for everyone | no | yes | yes | yes | only a call they started |
| Device push tokens: register / remove own | no | no | no | no | yes |
| **D-026:** the same WebSocket and voice calls in the web app (live chat, live pages, ringing, call dock) | **no** (Super Admin app has none) | yes | yes | yes | — (Guard app) |
| **D-027:** AI Investigation & Security Analytics Chatbot (read-only, no uploads): ask, follow up, investigate a case, delete own chats | **no** (`403 SUPER_ADMIN_NOT_ALLOWED`) | yes — whole own campus | yes — whole own campus | yes — operational scope only (no personnel, restricted-area access notes, user records, campus requests, organization settings or Admin-only audit records; own chatbot audit records only) | **no** (`403 STAFF_ONLY`) |
| Chat sessions of another user, campus or organization | no | no (`404`) | no (`404`) | no (`404`) | no |
| Low-storage notifications | — | yes | yes | yes | no |

Detections are accepted only from AI workers holding `DETECTION_INGEST_TOKEN`; no user token works
there. Camera status, recording segments, expiry lists and disk reports are accepted only from the ingestion
service and the retention daemon holding `INTERNAL_SERVICE_TOKEN` (D-023).

Personnel rows (D-016): "Main Admin" and "Additional Admin" are both Organization Admins of their own
campus. Supervisors read personnel and grants only through narrow database functions, so their
limit holds in the database as well as in the API. Module guide: `personnel_management.md`.

## Planned (requirements only; not built)

| Area | Documented intent | Open points |
|---|---|---|
| Evidence deletion by Supervisors | Admin or "authorized Supervisor" deletes (Section 17); built for Admins only (D-023) | How a Supervisor becomes authorized (Section 22); until decided, no Supervisor deletes |
| Face recognition for personnel | Section 14.5 (optional module) | Privacy and legal basis, consent, templates, review (D-016 item 9) |
| Main Admin vs Branch Admin matrix | Section 22 | Decided where built (D-024): only the Main Admin approves campuses and changes organization settings; every other Admin right is per campus. Anything not listed above stays open |
