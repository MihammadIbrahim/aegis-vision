// Decision D-024: the remaining Organization Admin and Supervisor pages (campus approvals, audit logs,
// alerts and evidence polish, dashboard overview, locations, imports, staff IDs, user records, person
// search, live overlay). Every figure comes from a mocked API response.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Role, Session } from "../api/types";
import { StaticAuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";
import { AlertDetailPage } from "../features/alerts/pages/AlertDetailPage";
import { AlertsPage } from "../features/alerts/pages/AlertsPage";
import { EscalateDialog } from "../features/alerts/components/EscalateDialog";
import type { Alert, EscalationBody } from "../features/alerts/types";
import type { Overlay } from "../features/ai-config/api";
import { AuditLogsPage } from "../features/audit-logs/pages/AuditLogsPage";
import { CampusRequestsPage } from "../features/campus-requests/pages/CampusRequestsPage";
import { DashboardPage } from "../features/dashboard/pages/DashboardPage";
import { LocationLinks } from "../features/locations/components/LocationLinks";
import { ImportHistoryPage } from "../features/personnel-import/pages/ImportHistoryPage";
import { SearchDetailPage } from "../features/person-search/pages/SearchDetailPage";
import { StaffMemberPage } from "../features/staff/pages/StaffMemberPage";
import { UserRecordsPage } from "../features/user-records/pages/UserRecordsPage";
import { DetectionLayer } from "../features/video/components/LiveTile";
import { parseTags, tagsProblem } from "../features/video/components/EvidenceFields";
import { EvidenceDetailPage } from "../features/video/pages/EvidenceDetailPage";
import { EvidencePage } from "../features/video/pages/EvidencePage";
import { mockApi, sent } from "./helpers";

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:test"), revokeObjectURL: vi.fn() }));
});

function session(role: Role, extra: Partial<Session> = {}): Session {
  return { user_id: "X", account_id: "U-SELF", full_name: role === "ADMIN" ? "Admin A" : "Supervisor S", role, dashboard: null, ...extra };
}

function show(ui: ReactElement, who: Session, path = "/", route?: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <StaticAuthProvider session={who}>
        <ToastProvider>
          {route ? (
            <Routes>
              <Route path={route} element={ui} />
              <Route path="*" element={<div>navigated away</div>} />
            </Routes>
          ) : (
            ui
          )}
        </ToastProvider>
      </StaticAuthProvider>
    </MemoryRouter>,
  );
}

function alertOf(id: string, extra: Partial<Alert> = {}): Alert {
  return {
    id, alert_type: "WEAPON", priority: "HIGH", status: "NEW", camera_id: "c1", camera_name: "Gate Camera",
    location: { building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR", floor_id: null, floor_name: null, area_id: null, area_name: null },
    confidence: "0.91", detected_at: "2026-11-01T10:00:00Z", last_detected_at: "2026-11-01T10:00:00Z", detection_count: 1,
    close_note: null, closed_at: null, created_at: "2026-11-01T10:00:00Z", updated_at: "2026-11-01T10:00:00Z", recipients: [],
    recipient_count: 3, acknowledged_count: 2, responding_count: 1, handled_by: null, handled_by_name: null, handled_at: null, has_snapshot: true,
    ...extra,
  };
}

// --- Campus approvals (Org Admin page 15) ----------------------------------------------------------------

const REQUEST = {
  id: "r1", proposed_campus_name: "North Campus", applicant_name: "Ali Applicant", applicant_email: "ali@acme.example",
  email_verified: true, status: "PENDING", submitted_at: "2026-11-01T10:00:00Z", reviewed_at: null, applicant_username: "ali",
  applicant_phone: null, email_verified_at: "2026-11-01T10:05:00Z", decision_deadline: "2026-11-15T10:00:00Z",
};
const EMPTY_HISTORY = { items: [], total: 0, limit: 25, offset: 0, counts: { PENDING: 1, APPROVED: 0, REJECTED: 0 } };

describe("Campus Requests (Main Admin)", () => {
  it("approves only after the password and the emailed code", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/campus-requests$/, body: [REQUEST] },
      { method: "GET", url: /\/campus-requests\/history/, body: EMPTY_HISTORY },
      { method: "POST", url: /\/campus-requests\/authorization$/, body: { id: "z1", email: "a***@acme.example", otp_expires_in_seconds: 300, resend_available_in_seconds: 60 } },
      { method: "POST", url: /\/campus-requests\/authorization\/z1\/verify$/, body: { authorization_id: "z1", form_expires_at: "2026-11-01T11:00:00Z" } },
      { method: "POST", url: /\/campus-requests\/r1\/approve$/, body: { ...REQUEST, status: "APPROVED" } },
    ]);
    show(<CampusRequestsPage />, session("ADMIN", { is_main_admin: true }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog", { name: "Approve “North Campus”" });
    fireEvent.change(within(dialog).getByLabelText(/Your current password/), { target: { value: "Main-pass-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send code" }));
    fireEvent.change(await within(dialog).findByLabelText(/Code from the email/), { target: { value: "123456" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Approve campus" }));
    await waitFor(() => expect(sent(calls, "POST", /\/r1\/approve$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/authorization$/)[0].body).toEqual({ current_password: "Main-pass-1" });
    expect(sent(calls, "POST", /\/verify$/)[0].body).toEqual({ otp: "123456" });
    expect(sent(calls, "POST", /\/r1\/approve$/)[0].body).toEqual({ authorization_id: "z1" });
  });

  it("rejects with the password only, and tells the reviewer the details are erased", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/campus-requests$/, body: [REQUEST] },
      { method: "GET", url: /\/campus-requests\/history/, body: EMPTY_HISTORY },
      { method: "POST", url: /\/campus-requests\/r1\/reject$/, body: { ...REQUEST, status: "REJECTED" } },
    ]);
    show(<CampusRequestsPage />, session("ADMIN", { is_main_admin: true }));
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = screen.getByRole("dialog", { name: "Reject “North Campus”" });
    expect(within(dialog).getByText(/erased permanently/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/Your current password/), { target: { value: "Main-pass-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject and erase details" }));
    await waitFor(() => expect(sent(calls, "POST", /\/reject$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/reject$/)[0].body).toEqual({ current_password: "Main-pass-1" });
  });

  it("shows other Admins a note and loads nothing", () => {
    const calls = mockApi([]);
    show(<CampusRequestsPage />, session("ADMIN", { is_main_admin: false }));
    expect(screen.getByText(/Only the organization's Main Admin reviews requests/)).toBeTruthy();
    expect(calls.filter((c) => c.url.includes("/campus-requests"))).toHaveLength(0);
  });
});

// --- Audit logs (Org Admin page 21, Supervisor page 17) ------------------------------------------------------

describe("Audit Logs", () => {
  it("sends the chosen filters to the server and shows each record's result", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/audit-logs\/categories$/, body: { categories: ["ALERTS", "CAMERAS"] } },
      {
        method: "GET",
        url: /\/audit-logs\?/,
        body: {
          items: [{ id: "l1", occurred_at: "2026-11-01T10:00:00Z", category: "CAMERAS", action: "CAMERA_UPDATED", outcome: "DENIED",
            actor_user_id: "u1", actor_display_id: "SUP-1", actor_name: "Supervisor S", actor_role: "SUPERVISOR", target_type: "CAMERA",
            target_id: "c1", target_label: "Gate Camera", details: {} }],
          total: 1, limit: 50, offset: 0,
        },
      },
    ]);
    show(<AuditLogsPage />, session("SUPERVISOR"));
    expect(await screen.findByText("Gate Camera")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Result"), { target: { value: "DENIED" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "GUARD" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("outcome=DENIED") && c.url.includes("actor_role=GUARD"))).toBe(true));
    expect(screen.getAllByText("Denied").length).toBeGreaterThan(0);
  });
});

// --- Alerts (page 4) -----------------------------------------------------------------------------------------

describe("Alerts list and detail", () => {
  it("searches by text, date range and 'handled by me', and shows Guard counts and the handler", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/locations$/, body: [] },
      { method: "GET", url: /\/alerts\?/, body: [alertOf("a1", { handled_by: "U-SELF", handled_by_name: "Supervisor S" })] },
    ]);
    show(<AlertsPage />, session("SUPERVISOR"));
    expect(await screen.findByText("3 sent · 2 ack. · 1 resp.")).toBeTruthy();
    expect(screen.getByText("Supervisor S")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Search alerts"), { target: { value: "gate" } });
    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-11-02" } });
    fireEvent.click(screen.getByLabelText(/Handled by me/));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=gate"))).toBe(true));
    const url = calls.filter((c) => c.url.includes("q=gate")).at(-1)!.url;
    expect(url).toContain("handled_by=U-SELF");
    expect(url).toMatch(/since=2026-1[01]-/);
    expect(url).toMatch(/until=2026-11-0[23]/);
  });

  it("shows the snapshot, escalations and cases, and lets staff take the alert", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/alerts\/a1$/, body: alertOf("a1") },
      { method: "GET", url: /\/alerts\/a1\/history$/, body: [] },
      { method: "GET", url: /\/alerts\/a1\/messages$/, body: [] },
      { method: "GET", url: /\/alerts\/a1\/snapshot$/, body: "jpeg" },
      {
        method: "GET",
        url: /\/alerts\/a1\/escalations$/,
        body: [{ id: "e1", scope: "AUTOMATIC", escalated_by_name: null, escalated_by_role: null, note: null, added_count: 2, created_at: "2026-11-01T10:02:00Z", guards: ["Bilal", "Sana"] }],
      },
      { method: "GET", url: /\/incidents\/assignable-staff$/, body: [{ user_id: "U-SELF", display_id: "SUP-1", full_name: "Supervisor S", role: "SUPERVISOR" }] },
      { method: "GET", url: /\/incidents\/for-alert\/a1$/, body: [] },
      { method: "PUT", url: /\/alerts\/a1\/handler$/, body: alertOf("a1", { handled_by: "U-SELF", handled_by_name: "Supervisor S" }) },
    ]);
    show(<AlertDetailPage />, session("SUPERVISOR"), "/alerts/a1", "/alerts/:id");
    expect(await screen.findByRole("img", { name: /Detection snapshot from Gate Camera/ })).toBeTruthy();
    expect(await screen.findByText("Automatic: no Guard acknowledged in time")).toBeTruthy();
    expect(screen.getByText("Bilal, Sana")).toBeTruthy();
    expect(screen.getByText("This alert is not part of any case.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open a case" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "I'll handle it" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/handler$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/handler$/)[0].body).toEqual({ user_id: "U-SELF" });
  });
});

describe("Escalation to chosen Guards", () => {
  it("finds active Guards by name and adds their IDs (item 20)", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/locations$/, body: [] },
      {
        method: "GET",
        url: /\/staff\?/,
        body: [{ id: "g1", display_id: "GRD-1", role: "GUARD", status: "ACTIVE", full_name: "Bilal Guard", username: "bilal", email: "b@x.example",
          phone_number: null, created_at: "2026-11-01T10:00:00Z", category: "STANDBY", location: null }],
      },
    ]);
    const submit = vi.fn(async (_body: EscalationBody) => undefined);
    show(<EscalateDialog onClose={() => undefined} onSubmit={submit} />, session("SUPERVISOR"));
    fireEvent.click(screen.getByLabelText(/Chosen Guards/));
    fireEvent.change(screen.getByLabelText(/Find a Guard/), { target: { value: "bil" } });
    fireEvent.click(await screen.findByRole("button", { name: "Add" }));
    expect(calls.some((c) => /\/staff\?role=GUARD&status=ACTIVE&q=bil/.test(c.url))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Escalate" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0]).toMatchObject({ scope: "GUARDS", guard_ids: ["GRD-1"] });
  });
});

// --- Evidence (pages 8–9) ------------------------------------------------------------------------------------

const ITEM = {
  id: "e1", kind: "CLIP", title: "Fight at the gate", notes: "", investigation_ref: null, source: "ALERT", alert_id: "a1",
  camera_id: "c1", camera_name: "Gate Camera", covers_from: "2026-11-01T10:00:00Z", covers_to: "2026-11-01T10:00:20Z",
  media_type: "video/mp4", size_bytes: 1000, sha256: "a".repeat(64), saved_by: "u1", saved_by_name: "Admin A",
  saved_at: "2026-11-01T10:05:00Z", updated_at: "2026-11-01T10:05:00Z", tags: ["gate"], incident_id: null, incident_reference: null,
  building_id: "b1", floor_id: null, area_id: null, location: "Admin Block",
};
const OPEN_CASES = { items: [{ id: "i1", reference: "INC-000001", title: "Gate fight" }], total: 1, limit: 100, offset: 0, counts: {} };

describe("Evidence Vault", () => {
  it("cleans tags like the server and explains invalid ones", () => {
    expect(parseTags(" Gate 3 , gate 3, night   shift,, ")).toEqual(["Gate 3", "night shift"]);
    expect(tagsProblem("ok, fine")).toBeNull();
    expect(tagsProblem("-bad")).toMatch(/starting with a letter or digit/);
    expect(tagsProblem(Array.from({ length: 11 }, (_, n) => `t${n}`).join(","))).toBe("At most 10 tags.");
  });

  it("filters by tag, location and case, and shows tags, case and location", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/cameras/, body: [] },
      { method: "GET", url: /\/locations$/, body: [] },
      { method: "GET", url: /\/incidents\?/, body: OPEN_CASES },
      { method: "GET", url: /\/evidence\?/, body: [{ ...ITEM, incident_id: "i1", incident_reference: "INC-000001" }] },
    ]);
    show(<EvidencePage />, session("ADMIN"));
    expect(await screen.findByRole("link", { name: "INC-000001" })).toBeTruthy();
    expect(screen.getByText("Admin Block")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "gate" }));
    await waitFor(() => expect(calls.some((c) => /\/evidence\?.*tag=gate/.test(c.url))).toBe(true));
    fireEvent.change(await screen.findByLabelText("Case"), { target: { value: "i1" } });
    await waitFor(() => expect(calls.some((c) => /\/evidence\?.*incident_id=i1/.test(c.url))).toBe(true));
  });

  it("saves edited tags and links the item to an open case", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/evidence\/e1$/, body: ITEM },
      { method: "GET", url: /\/evidence\/e1\/media$/, body: "mp4" },
      { method: "GET", url: /\/incidents\?/, body: OPEN_CASES },
      { method: "PATCH", url: /\/evidence\/e1$/, body: { ...ITEM, tags: ["gate", "night"] } },
      { method: "PUT", url: /\/incidents\/i1\/evidence\/e1$/, body: {} },
    ]);
    show(<EvidenceDetailPage />, session("SUPERVISOR"), "/evidence/e1", "/evidence/:id");
    const tags = await screen.findByLabelText(/^Tags/);
    fireEvent.change(tags, { target: { value: "gate, night" } });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    await waitFor(() => expect(sent(calls, "PATCH", /\/evidence\/e1$/)).toHaveLength(1));
    expect(sent(calls, "PATCH", /\/evidence\/e1$/)[0].body).toMatchObject({ tags: ["gate", "night"] });
    const caseSelect = await screen.findByLabelText(/^Open case/);
    await waitFor(() => expect(within(caseSelect).getByRole("option", { name: /INC-000001/ })).toBeTruthy());
    fireEvent.change(caseSelect, { target: { value: "i1" } });
    fireEvent.click(screen.getByRole("button", { name: "Link to case" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/incidents\/i1\/evidence\/e1$/)).toHaveLength(1));
  });
});

// --- Dashboard overview (page 1) ----------------------------------------------------------------------------

const FEED = {
  campus: "Main Campus",
  counters: {
    cameras_total: 5, cameras_online: 4, cameras_offline: 1, guards_total: 9, guards_active: 8, guards_stationed: 6,
    supervisors_total: 3, supervisors_active: 2, alerts_total: 12, alerts_high: 5, alerts_pending: 2, alerts_open: 4,
    incidents_active: 2, incidents_resolved: 7, incidents_opened: 3, manual_reports: 1,
  },
  urgent_alerts: [{ id: "a9", alert_type: "WEAPON", camera_name: "Gate Camera", status: "NEW", created_at: "2026-11-01T10:00:00Z" }],
  recent_alerts: [{ id: "a9", alert_type: "WEAPON", priority: "HIGH", status: "NEW", camera_name: "Gate Camera", location: "Admin Block", created_at: "2026-11-01T10:00:00Z", confidence: 0.9 }],
  recent_incidents: [{ id: "i1", reference: "INC-000001", title: "Gate fight", status: "OPEN", priority: "HIGH", assigned_name: null, updated_at: "2026-11-01T10:00:00Z" }],
  guards: [{ guard_id: "GRD-1", full_name: "Bilal Guard", stationed: true, location: "Admin Block" }, { guard_id: "GRD-2", full_name: "Sana Guard", stationed: false, location: null }],
  alerts_by_type: [{ alert_type: "WEAPON", priority: "HIGH", count: 4 }, { alert_type: "RESTRICTED_AREA", priority: "STANDARD", count: 2 }],
  daily: [{ day: "2026-10-31", high: 1, standard: 0, incidents: 1, manual_reports: 0 }, { day: "2026-11-01", high: 3, standard: 2, incidents: 2, manual_reports: 1 }],
  person_observations: 40,
  activity: [{ occurred_at: "2026-11-01T10:00:00Z", action: "CAMERA_UPDATED", category: "CAMERAS", actor_name: "Admin A", actor_role: "ADMIN", target_label: "Gate Camera", outcome: "SUCCESS" }],
};
const SUP_SUMMARY = {
  organization: { organization_id: "ABCDEF12345678901", name: "Acme Security", status: "ACTIVE" },
  campus: { campus_id: "c", name: "Main Campus", status: "ACTIVE" },
  supervisor: { user_id: "SUP1", full_name: "Supervisor S", username: "s", email: "s@acme.example", phone_number: null, role: "SUPERVISOR" },
  summary: { active_guards: 7, cameras: 3 },
  not_available_yet: [],
};

function tile(label: string): string {
  const el = screen.getByText(label, { selector: ".stat-label" }).closest(".stat-tile") as HTMLElement;
  return within(el).getByText(/./, { selector: ".stat-value" }).textContent ?? "";
}

describe("Dashboard overview", () => {
  it("shows the campus counters, the urgent banner, feeds, Guard availability and Supervisor quick actions", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/supervisor\/dashboard$/, body: SUP_SUMMARY },
      { method: "GET", url: /\/analytics\/dashboard$/, body: FEED },
      { method: "GET", url: /\/alerts\?/, body: [] },
      { method: "GET", url: /\/manual-reports\?/, body: [] },
      { method: "GET", url: /\/notifications\?/, body: [] },
      { method: "GET", url: /\/locations$/, body: [] },
      { method: "GET", url: /\/restricted-access\/areas/, body: [] },
      { method: "GET", url: /\/live\/cameras/, body: [] },
    ]);
    show(<DashboardPage />, session("SUPERVISOR"));
    await waitFor(() => expect(tile("Total cameras")).toBe("5"));
    expect(tile("Offline cameras")).toBe("1");
    expect(tile("Pending alerts")).toBe("2");
    expect(tile("Active incidents")).toBe("2");
    expect(tile("Resolved incidents")).toBe("7");
    expect(screen.queryByText("Total Supervisors")).toBeNull();
    const banner = screen.getByRole("alert", { name: "High-priority alerts" });
    expect(within(banner).getByText("1 high-priority alert still open")).toBeTruthy();
    expect(screen.getByRole("link", { name: /INC-000001/ })).toBeTruthy();
    expect(screen.getByText("Standby")).toBeTruthy();
    expect(screen.getByText("40")).toBeTruthy();
    const actions = screen.getByRole("navigation", { name: "Quick actions" });
    expect(within(actions).getByRole("link", { name: "Acknowledge Alert" }).getAttribute("href")).toBe("/alerts/a9");
    expect(within(actions).getByRole("link", { name: "Person Search" })).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/organization-admin/"))).toBe(false);
  });
});

// --- Locations, imports, staff IDs, user records --------------------------------------------------------------

const PATH = { level: "BUILDING", building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR", floor_id: null, floor_name: null, area_id: null, area_name: null };

describe("Locations, import mappings, Guard IDs and user records", () => {
  it("lists cameras and Stationed Guards by location and removes a Guard's post after confirmation", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/locations\/camera-links$/, body: [{ camera_id: "c1", display_name: "Gate Camera", location: PATH }] },
      { method: "GET", url: /\/locations\/guard-assignments$/, body: [{ guard_id: "GRD-1", full_name: "Bilal Guard", location: PATH }] },
      { method: "DELETE", url: /\/locations\/guard-assignments\/GRD-1$/, status: 204, body: null },
    ]);
    show(<LocationLinks tree={[]} />, session("SUPERVISOR"));
    expect(await screen.findByText("Gate Camera")).toBeTruthy();
    const guards = screen.getByRole("table", { name: "Stationed Guards" });
    fireEvent.click(await within(guards).findByRole("button", { name: "Remove" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove location" }));
    await waitFor(() => expect(sent(calls, "DELETE", /guard-assignments\/GRD-1$/)).toHaveLength(1));
  });

  it("lists and deletes saved import mappings", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/personnel-imports\/mapping-profiles$/, body: [{ id: "p1", name: "Registrar export", mapping: { "Roll No": "EXTERNAL_ID" } }] },
      { method: "GET", url: /\/personnel-imports\?/, body: [] },
      { method: "DELETE", url: /\/mapping-profiles\/p1$/, status: 204, body: null },
    ]);
    show(<ImportHistoryPage />, session("ADMIN"));
    expect(await screen.findByText("Roll No → external id")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete mapping" }));
    await waitFor(() => expect(sent(calls, "DELETE", /mapping-profiles\/p1$/)).toHaveLength(1));
  });

  it("gives a Guard a new ID with the caller's password", async () => {
    const calls = mockApi([
      {
        method: "GET",
        url: /\/staff\/g1$/,
        body: { id: "g1", display_id: "GRD-OLD", role: "GUARD", status: "ACTIVE", full_name: "Bilal Guard", username: "bilal", email: "b@acme.example",
          phone_number: null, created_at: "2026-11-01T10:00:00Z", category: "STANDBY", location: null },
      },
      { method: "POST", url: /\/staff\/guards\/g1\/regenerate-id$/, body: { display_id: "GRD-NEW" } },
    ]);
    show(<StaffMemberPage />, session("SUPERVISOR"), "/staff/g1", "/staff/:id");
    fireEvent.click(await screen.findByRole("button", { name: "New Guard ID" }));
    const dialog = screen.getByRole("dialog", { name: "Give Bilal Guard a new Guard ID" });
    fireEvent.change(within(dialog).getByLabelText(/Your current password/), { target: { value: "Sup-pass-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Generate new ID" }));
    await waitFor(() => expect(sent(calls, "POST", /regenerate-id$/)).toHaveLength(1));
    expect(sent(calls, "POST", /regenerate-id$/)[0].body).toEqual({ current_password: "Sup-pass-1" });
    expect(await screen.findByText("Bilal Guard's new Guard ID is GRD-NEW.")).toBeTruthy();
  });

  it("deletes a former user's data only with DELETE typed and the password", async () => {
    const left = { user_id: "u9", display_id: "GRD-9", role: "GUARD", status: "LEFT", full_name: "Old Guard", username: null, email: null,
      phone_number: null, created_at: "2026-01-01T00:00:00Z", deleted: false, deleted_at: null, location: null, status_changed_at: null };
    const calls = mockApi([
      { method: "GET", url: /\/user-records\?/, body: { items: [left], total: 1, limit: 50, offset: 0 } },
      { method: "POST", url: /\/user-records\/u9\/permanent-delete$/, status: 204, body: null },
    ]);
    show(<UserRecordsPage />, session("ADMIN"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete data" }));
    const dialog = screen.getByRole("dialog", { name: "Permanently delete Old Guard's data" });
    const submit = within(dialog).getByRole("button", { name: "Delete permanently" }) as HTMLButtonElement;
    fireEvent.change(within(dialog).getByLabelText(/Your current password/), { target: { value: "Admin-pass-1" } });
    expect(submit.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Type "DELETE" to confirm/), { target: { value: "DELETE" } });
    fireEvent.click(submit);
    await waitFor(() => expect(sent(calls, "POST", /permanent-delete$/)).toHaveLength(1));
    expect(sent(calls, "POST", /permanent-delete$/)[0].body).toEqual({ current_password: "Admin-pass-1", confirmation: "DELETE" });
  });
});

// --- Person search and live overlay (pages 7, 2) -----------------------------------------------------------------

describe("Person search results and the live overlay", () => {
  it("lets reviewers verify only likely matches", async () => {
    const candidate = (id: string, band: string) => ({
      id, observation_id: `o-${id}`, camera_id: "c1", camera_name: "Gate Camera", location: null, observed_at: "2026-11-01T10:00:00Z",
      similarity: band === "LIKELY_MATCH" ? 0.82 : 0.61, band, review: "PENDING", reviewed_by_name: null, reviewed_at: null,
      has_crop: false, incident_id: null, incident_reference: null, evidence_id: null,
    });
    mockApi([
      {
        method: "GET",
        url: /\/person-search\/s1$/,
        body: { id: "s1", kind: "OBSERVATION", created_at: "2026-11-01T11:00:00Z", created_by_name: "Admin A", period_from: "2026-11-01T00:00:00Z",
          period_to: "2026-11-01T11:00:00Z", camera_ids: [], note: null, has_reference: false, searched_observations: 20, complete: false,
          limitations: [{ camera_id: "c2", camera_name: "Back Camera", issue: "CAMERA_OFFLINE" }], candidates: [candidate("k1", "LIKELY_MATCH"), candidate("k2", "UNCERTAIN")] },
      },
    ]);
    show(<SearchDetailPage />, session("ADMIN"), "/person-search/s1", "/person-search/:id");
    expect(await screen.findByText(/This search is not complete/)).toBeTruthy();
    const verify = screen.getAllByRole("button", { name: "Verified match" }) as HTMLButtonElement[];
    expect(verify.map((b) => b.disabled)).toEqual([false, true]);
  });

  it("draws each detection with its label, track ID and confidence, and nothing when stale", () => {
    const overlay: Overlay = {
      camera_id: "c1", captured_at: "2026-11-01T10:00:00Z", stale: false, frame_width: 1280, frame_height: 720, modules: ["human_detection"],
      detections: [{ label: "person", confidence: 0.87, box: [0.1, 0.2, 0.3, 0.6], track_id: 7, module: "human_detection" }],
    };
    const { container, rerender } = render(<DetectionLayer overlay={overlay} />);
    expect(screen.getByRole("img", { name: "1 AI detection" })).toBeTruthy();
    expect(screen.getByText("person #7 87%")).toBeTruthy();
    expect(container.querySelector("rect")?.getAttribute("x")).toBe("128");
    rerender(<DetectionLayer overlay={{ ...overlay, stale: true }} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
