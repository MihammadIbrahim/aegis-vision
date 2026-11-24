// Phase 5: Admin and Supervisor dashboards. Every figure comes from a mocked API response here, so
// these tests also pin which endpoints each role calls.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Alert } from "../features/alerts";
import { DashboardPage } from "../features/dashboard/pages/DashboardPage";
import { summarizeAlerts, summarizeLocations, summarizeReports } from "../features/dashboard/metrics";
import type { ManualReport } from "../features/manual-reports";
import { mockApi, renderAs, type Call } from "./helpers";

function alert(id: string, status: Alert["status"], type: Alert["alert_type"] = "WEAPON"): Alert {
  return {
    id, alert_type: type, priority: type === "RESTRICTED_AREA" ? "STANDARD" : "HIGH", status,
    camera_id: "c1", camera_name: `Camera ${id}`,
    location: { building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR", floor_id: null, floor_name: null, area_id: null, area_name: null },
    confidence: null, detected_at: "2026-11-01T10:00:00Z", last_detected_at: "2026-11-01T10:00:00Z", detection_count: 1,
    close_note: null, closed_at: null, created_at: "2026-11-01T10:00:00Z", updated_at: "2026-11-01T10:00:00Z", recipients: [],
  };
}

function report(id: string, status: ManualReport["status"]): ManualReport {
  return {
    id, guard_id: "G1", guard_name: "Bilal Guard", report_type: `Report ${id}`, description: "x",
    building_id: "b1", building_name: "Admin Block", floor_id: null, floor_name: null, area_id: null, area_name: null,
    has_photo: false, occurred_at: null, status, review_note: null, reviewed_at: null, created_at: "2026-11-01T09:00:00Z",
  };
}

const TREE = [
  { id: "b1", name: "Admin Block", kind: "INDOOR", camera_count: 2, guard_count: 1,
    floors: [{ id: "f1", name: "Ground", level: 0, areas: [{ id: "a1", name: "Gate", floor_id: "f1" }, { id: "a2", name: "Lobby", floor_id: "f1" }] }],
    areas: [{ id: "a3", name: "Roof", floor_id: null }] },
  { id: "b2", name: "North Fence", kind: "OUTDOOR_PERIMETER", camera_count: 0, guard_count: 0, floors: [], areas: [{ id: "a4", name: "North Gate", floor_id: null }] },
];

const ORG = { organization_id: "ABCDEF12345678901", name: "Acme Security", status: "ACTIVE" };
const CAMPUS = { name: "Main Campus", status: "ACTIVE" };
const ADMIN_SUMMARY = {
  organization: { ...ORG, official_email: "o@acme.example", official_phone_number: null },
  campus: CAMPUS,
  admin: { user_id: "ADM1", full_name: "Admin A", username: "a", email: "a@acme.example", phone_number: null, role: "ADMIN", is_main_admin: true },
  summary: { active_admins: 2, active_supervisors: 3, active_guards: 7, cameras: 3, pending_campus_requests: 1 },
  not_available_yet: ["alerts", "guard_reports"],
};
const SUP_SUMMARY = {
  organization: ORG,
  campus: CAMPUS,
  supervisor: { user_id: "SUP1", full_name: "Supervisor S", username: "s", email: "s@acme.example", phone_number: null, role: "SUPERVISOR" },
  summary: { active_guards: 7, cameras: 3 },
  not_available_yet: ["alerts", "guard_reports"],
};
const AREAS = [{ area_id: "a1", area_name: "Server Room", building_id: "b1", building_name: "Admin Block", floor_id: null, floor_name: null,
  is_restricted: true, access_note: "SECRET ADMIN NOTE", marked_at: null, active_grants: 4 }];
const NOTES = [
  { id: "n1", kind: "ALERT_NEW", title: "Weapon alert at Gate Camera", alert_id: "x1", manual_report_id: null, created_at: "2026-11-01T10:00:00Z", read_at: null },
  { id: "n2", kind: "MANUAL_REPORT_SUBMITTED", title: "Manual report: Damage", alert_id: null, manual_report_id: "r1", created_at: "2026-11-01T09:00:00Z", read_at: "2026-11-01T09:10:00Z" },
];
const ALERTS = [alert("x1", "NEW"), alert("x2", "ESCALATED", "FIGHT_VIOLENCE"), alert("x3", "ACKNOWLEDGED", "RESTRICTED_AREA"), alert("x4", "RESOLVED")];
const REPORTS = [report("r1", "SUBMITTED"), report("r2", "REVIEWED")];

function shared(overrides: Record<string, unknown> = {}) {
  return [
    { method: "GET", url: /\/alerts\?limit=500&offset=0$/, body: overrides.alerts ?? ALERTS, status: (overrides.alertsStatus as number) ?? 200 },
    { method: "GET", url: /\/manual-reports\?limit=500&offset=0$/, body: REPORTS },
    { method: "GET", url: /\/notifications\?unread_only=false&limit=100$/, body: NOTES },
    { method: "GET", url: /\/locations$/, body: TREE },
    { method: "GET", url: /\/restricted-access\/areas\?include_unrestricted=false$/, body: AREAS },
  ];
}

function tile(label: string): string {
  const el = screen.getByText(label, { selector: ".stat-label" }).closest(".stat-tile") as HTMLElement;
  return within(el).getByText(/./, { selector: ".stat-value" }).textContent ?? "";
}

function urls(calls: Call[]): string[] {
  return calls.map((c) => c.url);
}

describe("dashboard metrics", () => {
  it("counts open alerts by status, type and priority", () => {
    const s = summarizeAlerts(ALERTS, 500);
    expect(s.open).toEqual({ value: 3, atLeast: false });
    expect(s.highPriorityOpen.value).toBe(2);
    expect(s.unacknowledged.value).toBe(1);
    expect(s.openByStatus).toEqual([
      { key: "NEW", value: 1 }, { key: "ACKNOWLEDGED", value: 1 }, { key: "RESPONDING", value: 0 }, { key: "ESCALATED", value: 1 }]);
    expect(s.openByType.map((t) => t.value)).toEqual([1, 1, 1]);
    expect(s.attention.map((a) => a.id)).toEqual(["x1", "x2", "x3"]);
  });

  it("marks open counts as 'at least' only when the page limit cut the open alerts", () => {
    expect(summarizeAlerts([alert("a", "NEW"), alert("b", "NEW")], 2).open).toEqual({ value: 2, atLeast: true });
    expect(summarizeAlerts([alert("a", "NEW"), alert("b", "RESOLVED")], 2).open).toEqual({ value: 1, atLeast: false });
    expect(summarizeAlerts([], 2).openIncomplete).toBe(false);
  });

  it("counts reports awaiting review, 'at least' when the list was cut", () => {
    expect(summarizeReports(REPORTS, 500).awaiting).toEqual({ value: 1, atLeast: false });
    expect(summarizeReports(REPORTS, 2).awaiting).toEqual({ value: 1, atLeast: true });
  });

  it("counts buildings, perimeters, floors and every area once", () => {
    expect(summarizeLocations(TREE as never)).toEqual({
      buildings: 1, perimeters: 1, floors: 1, areas: 4, camerasLinked: 2, guardsAssigned: 1 });
  });
});

describe("Admin dashboard", () => {
  it("shows the organization, operations, staff and site figures from the API", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/organization-admin\/dashboard$/, body: ADMIN_SUMMARY },
      { method: "GET", url: /\/personnel\?status=ACTIVE&limit=1&offset=0$/, body: { items: [], total: 42, limit: 1, offset: 0 } },
      ...shared(),
    ]);
    renderAs("ADMIN", <DashboardPage />);
    expect(await screen.findByText("Acme Security", { selector: "dd" })).toBeTruthy();
    expect(screen.getByText("Main Admin")).toBeTruthy();
    await waitFor(() => expect(tile("Open alerts")).toBe("3"));
    expect(tile("High-priority open")).toBe("2");
    expect(tile("Not yet acknowledged")).toBe("1");
    expect(tile("Reports awaiting review")).toBe("1");
    expect(tile("Unread notifications")).toBe("1");
    expect(tile("Active Admins")).toBe("2");
    expect(tile("Active Supervisors")).toBe("3");
    await waitFor(() => expect(tile("Active personnel")).toBe("42"));
    expect(tile("Pending campus requests")).toBe("1");
    expect(tile("Cameras")).toBe("3");
    expect(screen.getByText("1 without a location")).toBeTruthy();
    expect(screen.getByText("1 with an assigned location")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Manage access" })).toBeTruthy();
    expect(screen.queryByText("SECRET ADMIN NOTE")).toBeNull();
    expect(screen.getByText("Weapon alert at Gate Camera")).toBeTruthy();
    // No request names an organization or campus: the server decides from the session.
    expect(urls(calls).some((u) => u.includes("ABCDEF") || u.includes("campus"))).toBe(false);
    expect(urls(calls).some((u) => u.includes("/supervisor/dashboard"))).toBe(false);
  });

  it("hides campus requests from Admins who are not the Main Admin", async () => {
    mockApi([
      { method: "GET", url: /\/organization-admin\/dashboard$/, body: {
        ...ADMIN_SUMMARY, admin: { ...ADMIN_SUMMARY.admin, is_main_admin: false }, summary: { ...ADMIN_SUMMARY.summary, pending_campus_requests: null } } },
      { method: "GET", url: /\/personnel\?/, body: { items: [], total: 0, limit: 1, offset: 0 } },
      ...shared(),
    ]);
    renderAs("ADMIN", <DashboardPage />);
    expect(await screen.findByText("Organization Admin")).toBeTruthy();
    expect(screen.queryByText("Pending campus requests")).toBeNull();
  });

  it("shows a loading state, then an error with retry when the summary fails", async () => {
    mockApi([{ method: "GET", url: /\/organization-admin\/dashboard$/, status: 503, body: { detail: "Service unavailable." } }, ...shared()]);
    renderAs("ADMIN", <DashboardPage />);
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(await screen.findByText("Service unavailable.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("Supervisor dashboard", () => {
  it("uses only the Supervisor's endpoints and shows no Admin or personnel figures", async () => {
    const calls = mockApi([{ method: "GET", url: /\/supervisor\/dashboard$/, body: SUP_SUMMARY }, ...shared()]);
    renderAs("SUPERVISOR", <DashboardPage />);
    expect(await screen.findByText("Supervisor (whole campus)")).toBeTruthy();
    await waitFor(() => expect(tile("Open alerts")).toBe("3"));
    expect(tile("Active Guards")).toBe("7");
    for (const hidden of ["Active Admins", "Active Supervisors", "Active personnel", "Pending campus requests"]) {
      expect(screen.queryByText(hidden)).toBeNull();
    }
    expect(screen.getByRole("link", { name: "Who is authorized" })).toBeTruthy();
    expect(screen.getByText(/4 current access grants/)).toBeTruthy();
    expect(screen.queryByText("SECRET ADMIN NOTE")).toBeNull();
    const requested = urls(calls);
    expect(requested.some((u) => u.includes("/organization-admin/"))).toBe(false);
    expect(requested.some((u) => /\/personnel(\?|\/|$)/.test(u))).toBe(false);
    expect(requested.some((u) => u.includes("include_unrestricted=true") || u.includes("include_revoked"))).toBe(false);
  });

  it("keeps the other sections when one source fails, and shows empty states", async () => {
    mockApi([
      { method: "GET", url: /\/supervisor\/dashboard$/, body: SUP_SUMMARY },
      ...shared({ alerts: { detail: "Alerts are unavailable." }, alertsStatus: 500 }),
    ]);
    renderAs("SUPERVISOR", <DashboardPage />);
    expect(await screen.findAllByText("Alerts are unavailable.")).toHaveLength(2);
    expect(tile("Open alerts")).toBe("Unavailable");
    await waitFor(() => expect(tile("Reports awaiting review")).toBe("1"));
    expect(screen.getByText("Report r1")).toBeTruthy();
  });

  it("says plainly when there are no open alerts", async () => {
    mockApi([{ method: "GET", url: /\/supervisor\/dashboard$/, body: SUP_SUMMARY }, ...shared({ alerts: [alert("z", "RESOLVED")] })]);
    renderAs("SUPERVISOR", <DashboardPage />);
    await waitFor(() => expect(tile("Open alerts")).toBe("0"));
    expect(screen.getAllByText("No open alerts.").length).toBeGreaterThan(0);
  });
});
