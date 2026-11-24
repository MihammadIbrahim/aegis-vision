# Approved clarifications and decisions: Organization Admin authentication

**Approved by:** the project owner, 2026-10-27 (Stage 2 instructions, after the Stage 1 audit).
**Recorded by:** Claude Code, 2026-10-27.

**This is not an original requirements document.** It records decisions the project owner
approved after the five original documents in this folder were written. Those documents are
unchanged. Where a decision below differs from them, the decision applies to the topic it
covers. Each entry names the original section it adds to or changes.

"Stage" refers to the Organization Admin authentication task:
- Stage 2: first Admin registration and email OTP.
- Stage 3: Organization Admin login.
- Stage 4: additional Admins and approval.

| # | Decision | Stage | Relation to the original documents |
|---|---|---|---|
| A | First Admin registration fields; the first Admin becomes the Main Admin | 2 | Adds detail to Section 8 |
| B | Organization ID = 6 capital letters + 11 digits (amended; first approved as `ORG-` + 11 digits) | 2 | New: no format existed |
| C | Admin and Supervisor user ID = 6 capital letters + 11 digits | 2 (Admin), later (Supervisor) | **Changes Section 10** |
| D | Organization name rules | 2 (uniqueness), 3–4 (matching) | Adds to Sections 8 and 22 |
| E | "Main Campus" created automatically | 2 | New |
| F | Organization contacts start as the first Admin's contacts | 2 (storage), later (editing) | Adds to Section 8 |
| G | Additional Admin applies for a new campus | 4 | Confirms Section 8 |
| H | Approval needs password **and** a fresh email OTP | 4 | **Adds to Org Admin page 15, item 9** |
| I | Rejection and 72-hour expiry delete the applicant's data | 4 | New |
| J | `ADMIN_REQUEST_REJECTED` and the minimum kept after deletion | 4 | New; design proposal awaiting review |
| K | Admin login = Organization Name + Organization ID + email + password + email OTP | 3 | **Stricter than Section 9.1** |
| L | Pending Admin login returns `ADMIN_APPROVAL_PENDING` | 3–4 | Adds to Section 8 |

---

## A. First Organization Admin registration

The registration form has exactly these inputs:
- Organization Name
- Admin Full Name
- Username
- Email Address
- Phone Number
- Password

After the form, the Admin completes email OTP verification.

- The first Admin becomes the organization's **Main Admin**, matching Section 5 ("The first
  Admin of an organization will be the Main Admin by default") and Section 8.
- **No organization, campus or Admin account exists until the email OTP has been verified.**
  No session or token is issued by registration at all. Login is Stage 3.
- The email must be verified by OTP. The phone number is required but is **not** verified by
  SMS or phone OTP. This matches Section 8.

## B. Organization ID

> **Amended 2026-10-27 by the project owner.** New Organization IDs are **6 capital letters (A–Z)
> followed by 11 digits**, with no prefix or separator: 17 characters, for example
> `ABCDEF12345678901`. IDs already issued in the first approved format below (`ORG-` + 11 digits)
> are not rewritten and stay valid. That includes IDs reserved by registrations still waiting for
> their code. Implemented by migration `0005`. The rest of this decision is unchanged.
>
> The new format has the same shape as Admin and Supervisor user IDs (decision C). The format
> alone cannot tell the two kinds of ID apart, so every API field must say which ID it holds.

- **Format (as first approved):** `ORG-` followed by 11 random digits, for example `ORG-48271930561`.
- **Generation:** a cryptographically secure random generator, checked for uniqueness.
- **Permanent:** the ID is fixed to its organization forever and is never changed or reused.
- **Retries:** a retry of the same registration, such as a new code or a second verify
  attempt, never produces a new Organization ID.
- **Separate identifier:** it is not the database primary key, not a campus ID and not a
  user ID.
- **Returned** once registration and verification succeed.

The originals mention an "Organization ID / Unique Code" (Super Admin page 3) and a "Unique
Org Code" (Org Admin page 25) but define no format. This decision supplies one.

## C. Admin and Supervisor user ID format

- **New format for Admin and Supervisor accounts:** 6 capital letters followed by 11 digits,
  for example `KQMTZX48271930561`.
- **What this changes:** Section 10 lists `ADM-` + 11 digits for Admins and `SUP-` + 11 digits
  for Supervisors. For Admins and Supervisors this decision replaces those formats.
- **Different from the Organization ID** in decision B.
- **Guard ID: undecided.** The project owner will define it later. Until then, Guards keep
  the existing `GRD-` + 11 digits.
- **Existing IDs:** IDs already issued in the old `ADM-`/`SUP-` formats stay valid and are
  not rewritten, so existing data and history stay linked.
- **Supervisor format:** recorded here. Supervisor accounts are created and given IDs in a
  later task, not in this one.

## D. Organization name rules

1. **Uniqueness:** names must be unique ignoring capitalization. `Riverside University` and
   `riverside university` cannot both exist. A duplicate is refused with
   `ORGANIZATION_NAME_TAKEN`.
2. **Stored as entered:** the name is saved exactly as typed and never silently re-cased,
   re-spaced or re-spelled. A name with leading, trailing or double spaces is refused with an
   explanation rather than changed.
3. **Login (Stage 3):** the submitted name must match the registered name exactly, including
   spelling and capitalization.
4. **Joining an existing organization (Stage 4):** this uses the exact registered name. An
   incorrect or unknown name returns `ORGANIZATION_NOT_FOUND`.

These rules add to Section 8 ("exact name of the original organization — the same spelling
entered by the first Admin"). They also settle part of the Section 22 item "Duplicate
organization checks". Proving that an applicant belongs to the organization is still covered
by the Main Admin approval in decisions G and H. Section 8's rule that "entering the exact
name alone will not be enough to grant access" still holds.

## E. Main Campus

- After the first Admin is verified, the system creates the organization's first campus,
  named **`Main Campus`**. The registration form has no campus field.
- The Main Admin belongs to Main Campus.
- Campus IDs and the Organization ID are separate.
- Retries, new codes and duplicate requests must never create a second organization, Main
  Campus or first Admin.

## F. Organization contact information

- At registration, the first Admin's email and phone also become the organization's official
  email and phone.
- They are stored in **separate fields** from the Admin's own login email and phone, so either
  can change later without changing the other.
- **Editing (not in Stage 2):** only an authorized Admin may change the organization's contact
  details, and only after signing in and reaching the dashboard.
- **Related, unchanged:** Super Admin page 4 lets the Super Admin edit the *Organization
  Admin's* contact information. That concerns the Admin's contacts, not the organization's,
  and this decision does not change it.

## G. Additional Admin registration: Stage 4

- The option on the future sign-in page is labelled **"Already Registered Organization?"**
- **What the applicant gives:**
  - the exact registered organization name;
  - their full name, username, email and password;
  - the **proposed branch, campus or site name**, as Section 8 requires.

  They then verify their email by OTP.
- **New campus:** an additional Admin applies for a **new campus**, never for the Main Admin's
  existing campus.
- **On approval:** the new campus is created, and the approved Admin manages **only** that
  campus. Approval never grants access to another campus. This keeps the strict campus
  isolation decided earlier.
- **Before approval:** an email-verified application stays pending. A pending applicant gets
  no usable organization session and cannot reach the login OTP step.
- **After approval:** the Admin uses the normal login in decision K.
- **Name matching** follows decision D.

## H. Main Admin approval security: Stage 4

- **To approve** an additional Admin, the Main Admin must give both:
  1. their **current password**;
  2. a **fresh email OTP** sent to their own registered email.
- **OTP rules:** expiry, resend cooldown, attempt limits, single use and rate limits all apply.
- **Who:** only the Main Admin, and only for requests belonging to their own organization. The
  existing authorization and audit checks stay.

Org Admin page 15, item 9 requires only the password. This decision adds the OTP.

**Rejection: proposal, to confirm in Stage 4.** Rejection permanently deletes the applicant's
data (decision I), so it is as consequential as approval. The proposal is to require the same
password and fresh OTP for rejection.

## I. Rejection and 72-hour expiry: Stage 4

- **On rejection:** permanently delete the applicant's personal data and pending credentials
  from the operational database. That is:
  - name, username, email and phone (if collected);
  - password hash and OTP challenges;
  - proposed campus name and any other information the applicant provided.
- **On expiry:** an email-verified request not approved within **72 hours of successful email
  verification** is expired automatically and cleaned up the same way.
  - The timer starts at email verification, **not** at form submission.
  - An expired request can never be approved or used to log in, even if the cleanup job has
    not run yet. Every check compares against the 72-hour deadline itself.

## J. `ADMIN_REQUEST_REJECTED` and minimal retained data: Stage 4, proposal for review

**The requirement.** A later login attempt by a rejected applicant should return
`ADMIN_REQUEST_REJECTED`, yet the applicant's data must be deleted.

**Conflict to note.** Stage 1 recommended returning this error only after a correct password.
That is impossible once the password hash is deleted, as decision I requires. Some loss of
precision is unavoidable. The proposal:

**Proposed design.** Keep one small "tombstone" row per rejected or expired request, with only:

| Field | Purpose |
|---|---|
| organization (internal reference) | Which organization refused the request |
| email fingerprint | A keyed hash (HMAC-SHA-256 with a server-side secret) of the normalized email. The email itself cannot be read back from it without the server key, but the server can check whether a login email matches |
| outcome (`REJECTED` or `EXPIRED`) | Which error message to show |
| decided at | When the decision or expiry happened |
| delete after | When the tombstone itself is removed |

**What is not kept:** name, username, email in readable form, phone, password hash, OTPs,
proposed campus name, or the rest of the application.

**How login would use it (Stages 3–4).**
1. The organization name and Organization ID must be correct first.
2. Then, if no active account matches the email, the tombstone for that organization and email
   fingerprint is checked.
3. If one exists, the response is `ADMIN_REQUEST_REJECTED` (or an "expired" equivalent).
   Otherwise the normal generic invalid-credentials error is returned.

**Trade-off.** Without the password, anyone who knows an organization's exact name, its
Organization ID and an applicant's email could learn that this email's request was rejected.

**To decide in Stage 4:**
- how long a tombstone is kept (for example 30 days), after which login falls back to the
  generic error;
- whether a rejected email may apply again (proposal: yes; a new verified application replaces
  the tombstone).

## K. Organization Admin login: Stage 3

Login requires:
- the **Organization Name**, matched exactly;
- the **Organization ID**;
- the registered **email address**;
- the **password**;
- then an **email OTP**, on every login.

A token or session is issued only after the OTP check succeeds. A wrong organization name,
Organization ID, email or password never allows login.

Section 9.1 requires only "registered email or role-specific login identifier", the password
and the email OTP. This decision is stricter and does not break any other rule. The login
fields for Supervisors will be decided separately.

## L. Pending applicants at login: Stages 3–4

- **Pending:** an email-verified applicant who is not yet approved, and who logs in with
  correct credentials, gets the stable error code `ADMIN_APPROVAL_PENDING` with a readable
  message. They get no usable session and no login OTP.
- **After approval:** their next login attempt follows the normal flow in decision K. Approval
  never starts a session for them automatically.

This comes from the project owner's Organization Admin authentication task (Case A to Case C).

---

## Still open

| Item | Stage |
|---|---|
| The existing campus-approval tests will need the new password (and OTP) fields in their requests once decision H is built. Permission to update those requests is to be asked in Stage 4 | 4 |
| Whether rejection requires password + OTP (decision H proposal) | 4 |
| How long rejection tombstones are kept, and whether rejected emails may reapply (decision J) | 4 |
| Guard ID format (decision C) | Later — **settled by decision T** (2026-10-27) |
| Supervisor login fields (decision K) | Later — **settled by decision O** (2026-10-27) |
| Section 22 items not covered here stay open | — |

---

## Amendments of 2026-10-27 (Supervisor creation, Supervisor sign-in, password reset)

Approved by the project owner in the Supervisor task.

- **M. Supervisor ID.** New Supervisor IDs are 6 capital letters + 21 digits (27 characters), generated
  as one ID. This replaces the 6 + 11 format planned for Supervisors in decision C. Admin IDs stay 6 + 11.
  Earlier `SUP-` IDs stay valid. *(Changes Section 10 and decision C.)*
- **N. Supervisor creation.**
  - **Admin re-confirmation:** before the form opens, the Admin re-enters the current password and the
    code emailed to the Admin.
  - **The form:** Name, Username, Email, Phone Number and **Password**. The Admin sets the initial
    password. Section 9.2 says the Supervisor sets it through a secure setup process; the Supervisor
    can change it with forgot-password.
  - **On completion:** the Supervisor's email is verified by code, then the account is created in the
    Admin's own organization and campus.
- **O. Supervisor sign-in.** Supervisors use the Organization Admin sign-in (Organization Name +
  Organization ID + email + password + email code). `GET /api/v1/auth/me` tells the client which
  dashboard belongs to the role.
- **P. Forgot password (Admins and Supervisors).**
  - **What is entered:** Organization ID + registered email, then the emailed code, then the new
    password.
  - **No account disclosure:** the answers never reveal whether an account exists.
  - **After a reset:** every earlier session is refused. Guards are not covered yet. *(Implements
    Section 9.6 for these two roles.)*

## Amendments of 2026-10-27 (Supervisor profile)

- **Q. Supervisor ID is permanent.** A Supervisor ID can never be edited; the database refuses any change.
  For Supervisors, this replaces the "regenerate own ID through Settings" rule of Section 10.
- **R. Supervisor profile.**
  - **Editable:** Name, Username, Email and Password.
  - **Not editable:** Organization Name, Organization ID and Supervisor ID.
  - **Saving:** Save needs the current password, then the code sent to the registered email. A new
    email also needs the code sent to that new address.
  - **When changes apply:** only after verification. A failed, expired or cancelled verification
    changes nothing.
  - **Password changes:** a new password signs out every earlier session. *(Adds to Section 9.4 for
    Supervisors.)*
- **S. Forgot password with Supervisor ID.** Supervisors may reset with Supervisor ID + registered email.
  The Organization ID + email form (decision P) still works for Admins and Supervisors.

## Amendments of 2026-10-27 (Guards)

- **T. Guard ID.** 6 capital letters + 21 digits (27 characters), generated as one ID. Earlier `GRD-` IDs
  stay valid. *(Settles the Guard format left open in decision C; changes Section 10.)*
- **U. Guard creation.**
  - **Who:** an Organization Admin or a Supervisor, in their own organization and campus.
  - **Re-confirmation:** before the form opens, the actor re-enters the current password and the code
    emailed to them.
  - **The form:** Name, Username, Email, Phone Number, Password. The actor sets the first password.
  - **On completion:** the account is created after the Guard's email code is verified.
- **V. Guard sign-in.**
  - **What is entered:** Guard ID + registered email + password, then an email code. Section 7 allows
    "Guard ID **or** email"; this decision requires both.
  - **Guard app:** sign-in, forgot password and the Guard dashboard.
- **W. Guard forgot password.** Guard ID + registered email, then a code, then the new password.
- **X. Guard profile.**
  - **Editable by the Guard:** email, phone number and password only. Section 7 says a Guard cannot edit
    their own profile; this decision allows these three.
  - **Never editable here:** name, username, Guard ID, organization and campus.
  - **Saving:** the same rules as the Supervisor profile (decision R): current password, a code to the
    registered email, a code to a new email, and applied only after verification.
