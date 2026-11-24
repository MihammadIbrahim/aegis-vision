// Phase 6: alerts, manual reports and notifications for Admins and Supervisors.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "../app/App";
import { StaticAuthProvider } from "../auth/AuthContext";
import { canManageAlerts, navFor } from "../auth/permissions";
import { parseGuardIds } from "../features/alerts/components/EscalateDialog";
import { AlertDetailPage } from "../features/alerts/pages/AlertDetailPage";
import { AlertsPage } from "../features/alerts/pages/AlertsPage";
import type { Alert } from "../features/alerts";
import { ReportDetailPage } from "../features/manual-reports/pages/ReportDetailPage";
import { ReportsPage } from "../features/manual-reports/pages/ReportsPage";
import type { ManualReport } from "../features/manual-reports";
import { NotificationBell } from "../features/notifications";
import { NotificationsPage } from "../features/notifications/pages/NotificationsPage";
import { mockApi, renderAs, sent, sessionFor } from "./helpers";

const TREE = [
  {
    id: "b1",
    name: "Admin Block",
    kind: "INDOOR",
    floors: [{ id: "f1", name: "Ground", level: 0, areas: [{ id: "a1", name: "Main Gate", floor_id: "f1" }] }],
    areas: [],
  },
];

function alert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: "al1",
    alert_type: "WEAPON",
    priority: "HIGH",
    status: "NEW",
    camera_id: "c1",
    camera_name: "Gate Camera",
    location: {
      building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR",
      floor_id: "f1", floor_name: "Ground", area_id: "a1", area_name: "Main Gate",
    },
    confidence: "0.9100",
    detected_at: "2026-11-01T10:00:00Z",
    last_detected_at: "2026-11-01T10:02:00Z",
    detection_count: 3,
    close_note: null,
    closed_at: null,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-01T10:02:00Z",
    recipients: [
      {
        guard_id: "GRDABC1", full_name: "Bilal Guard", reason: "LOCATION", state: "ACKNOWLEDGED",
        assessment: "ADDITIONAL_GUARDS_REQUIRED", acknowledged_at: "2026-11-01T10:01:00Z", responding_at: null,
      },
    ],
    ...overrides,
  };
}

const HISTORY = [
  { event_type: "CREATED", actor_role: "SYSTEM", actor_name: null, from_status: null, to_status: "NEW",
    details: { alert_type: "WEAPON", priority: "HIGH", confidence: 0.91 }, created_at: "2026-11-01T10:00:00Z" },
  { event_type: "ROUTED", actor_role: "SYSTEM", actor_name: null, from_status: null, to_status: null,
    details: { guards: 1, camera_has_location: true }, created_at: "2026-11-01T10:00:01Z" },
  { event_type: "ASSESSMENT", actor_role: "GUARD", actor_name: "Bilal Guard", from_status: "ACKNOWLEDGED", to_status: "ACKNOWLEDGED",
    details: { action: "ADDITIONAL_GUARDS_REQUIRED" }, created_at: "2026-11-01T10:01:30Z" },
];

const DETAIL = (a: Alert) => [
  { method: "GET", url: /\/alerts\/al1$/, body: a },
  { method: "GET", url: /\/alerts\/al1\/history$/, body: HISTORY },
  { method: "GET", url: /\/alerts\/al1\/messages$/, body: [] },
  { method: "GET", url: /\/locations$/, body: TREE },
];

function report(overrides: Partial<ManualReport> = {}): ManualReport {
  return {
    id: "r1", guard_id: "GRDABC1", guard_name: "Bilal Guard", report_type: "Suspicious Person",
    description: "Someone near the back door.", building_id: "b1", building_name: "Admin Block",
    floor_id: null, floor_name: null, area_id: null, area_name: null, has_photo: false,
    occurred_at: null, status: "SUBMITTED", review_note: null, reviewed_at: null, created_at: "2026-11-01T09:00:00Z",
    ...overrides,
  };
}

describe("permissions", () => {
  it("offers alerts, reports and notifications to Admins and Supervisors only", () => {
    expect(canManageAlerts("ADMIN")).toBe(true);
    expect(canManageAlerts("SUPERVISOR")).toBe(true);
    expect(canManageAlerts("GUARD")).toBe(false);
    expect(navFor("GUARD")).toEqual([]);
    expect(navFor("SUPERVISOR").slice(0, 6).map((i) => i.to)).toEqual([
      "/dashboard", "/alerts", "/incidents", "/assistant", "/manual-reports", "/notifications",
    ]);
  });

  it("routes a Supervisor to the alert list", async () => {
    mockApi([[/\/notifications/, []], [/\/locations$/, TREE], [/\/alerts\?/, []]]);
    render(
      <MemoryRouter initialEntries={["/alerts"]}>
        <StaticAuthProvider session={sessionFor("SUPERVISOR")}>
          <App />
        </StaticAuthProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: "Alerts" })).toBeTruthy();
    expect(await screen.findByText("No alerts match.")).toBeTruthy();
  });
});

describe("alert list", () => {
  it("sends the chosen filters and links each alert to its details", async () => {
    const calls = mockApi([[/\/locations$/, TREE], [/\/alerts\?/, [alert()]]]);
    renderAs("SUPERVISOR", <AlertsPage />);
    const link = await screen.findByRole("link", { name: /Weapon detected/ });
    expect(link.getAttribute("href")).toBe("/alerts/al1");
    expect(screen.getByText("Admin Block › Ground › Main Gate")).toBeTruthy();
    expect(sent(calls, "GET", /\/alerts\?/)[0].url).toBe("/api/v1/alerts?limit=26&offset=0");

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "NEW" } });
    fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "HIGH" } });
    fireEvent.change(screen.getByLabelText("Building"), { target: { value: "b1" } });
    fireEvent.change(screen.getByLabelText("Floor"), { target: { value: "f1" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(sent(calls, "GET", /\/alerts\?/)).toHaveLength(2));
    expect(sent(calls, "GET", /\/alerts\?/)[1].url).toBe(
      "/api/v1/alerts?status=NEW&priority=HIGH&building_id=b1&floor_id=f1&limit=26&offset=0",
    );
  });

  it("pages with one extra row because the list has no total", async () => {
    const page = Array.from({ length: 26 }, (_, i) => alert({ id: `al${i}` }));
    const calls = mockApi([[/\/locations$/, TREE], [/offset=0/, page], [/offset=25/, [alert({ id: "last" })]]]);
    renderAs("ADMIN", <AlertsPage />);
    expect(await screen.findAllByRole("link", { name: /Weapon detected/ })).toHaveLength(25);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getAllByRole("link", { name: /Weapon detected/ })).toHaveLength(1));
    expect(sent(calls, "GET", /offset=25/)).toHaveLength(1);
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("alert details", () => {
  it("shows the camera, location, confidence, recipients and history", async () => {
    mockApi(DETAIL(alert()));
    renderAs("SUPERVISOR", <AlertDetailPage />, "/alerts/al1", "/alerts/:id");
    expect(await screen.findByText("91%")).toBeTruthy();
    expect(screen.getByText("Bilal Guard", { selector: "td" })).toBeTruthy();
    expect(screen.getAllByText("Additional Guards required").length).toBeGreaterThan(0);
    expect(screen.getByText("Alert raised by detection")).toBeTruthy();
    expect(await screen.findByText(/1 Guard$/)).toBeTruthy();
  });

  it("lets a Supervisor resolve with an optional note", async () => {
    const calls = mockApi([...DETAIL(alert()), { method: "POST", url: /resolve$/, body: alert({ status: "RESOLVED" }) }]);
    renderAs("SUPERVISOR", <AlertDetailPage />, "/alerts/al1", "/alerts/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));
    const dialog = screen.getByRole("dialog", { name: "Resolve this alert?" });
    await userEvent.type(within(dialog).getByLabelText(/Note/), "Checked on site");
    fireEvent.click(within(dialog).getByRole("button", { name: "Resolve alert" }));
    await waitFor(() => expect(sent(calls, "POST", /resolve$/)).toHaveLength(1));
    expect(sent(calls, "POST", /resolve$/)[0].body).toEqual({ note: "Checked on site", client_action_id: expect.any(String) });
    expect(await screen.findByText("Alert resolved.")).toBeTruthy();
  });

  it("requires a reason to dismiss", async () => {
    const calls = mockApi([...DETAIL(alert()), { method: "POST", url: /dismiss$/, body: alert({ status: "DISMISSED" }) }]);
    renderAs("ADMIN", <AlertDetailPage />, "/alerts/al1", "/alerts/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    const dialog = screen.getByRole("dialog", { name: "Dismiss this alert?" });
    const confirm = within(dialog).getByRole("button", { name: "Dismiss alert" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "False alarm: umbrella");
    fireEvent.click(confirm);
    await waitFor(() => expect(sent(calls, "POST", /dismiss$/)).toHaveLength(1));
    expect(sent(calls, "POST", /dismiss$/)[0].body).toEqual({ reason: "False alarm: umbrella", client_action_id: expect.any(String) });
  });

  it("escalates to a location of the campus and reports how many Guards were added", async () => {
    const calls = mockApi([
      ...DETAIL(alert()),
      { method: "POST", url: /escalate$/, body: { alert: alert({ status: "ESCALATED" }), added_guards: 2 } },
    ]);
    renderAs("SUPERVISOR", <AlertDetailPage />, "/alerts/al1", "/alerts/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Escalate" }));
    const dialog = screen.getByRole("dialog", { name: "Escalate alert" });
    fireEvent.click(within(dialog).getByLabelText(/Guards of a location/));
    const submit = within(dialog).getByRole("button", { name: "Escalate" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(await within(dialog).findByLabelText("Building"), { target: { value: "b1" } });
    fireEvent.change(within(dialog).getByLabelText("Floor"), { target: { value: "f1" } });
    fireEvent.click(submit);
    await waitFor(() => expect(sent(calls, "POST", /escalate$/)).toHaveLength(1));
    expect(sent(calls, "POST", /escalate$/)[0].body).toEqual({
      scope: "LOCATION",
      location: { building_id: "b1", floor_id: "f1", area_id: null },
      guard_ids: [],
      note: null,
      client_action_id: expect.any(String),
    });
    expect(await screen.findByText("Alert escalated: 2 Guards added.")).toBeTruthy();
  });

  it("keeps the dialog open with the server's message when nobody matches", async () => {
    mockApi([
      ...DETAIL(alert()),
      { method: "POST", url: /escalate$/, status: 422, body: { detail: "No other active Guard of this campus matches; nobody was added.", code: "NO_ELIGIBLE_GUARDS" } },
    ]);
    renderAs("SUPERVISOR", <AlertDetailPage />, "/alerts/al1", "/alerts/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Escalate" }));
    const dialog = screen.getByRole("dialog", { name: "Escalate alert" });
    fireEvent.click(within(dialog).getByLabelText(/Chosen Guards/));
    await userEvent.type(within(dialog).getByLabelText(/Guard IDs/), "GRD1, GRD2");
    fireEvent.click(within(dialog).getByRole("button", { name: "Escalate" }));
    expect(await within(dialog).findByText(/nobody was added/)).toBeTruthy();
  });

  it("hides every action and the message box once closed", async () => {
    mockApi(DETAIL(alert({ status: "DISMISSED", closed_at: "2026-11-01T11:00:00Z", close_note: "False alarm" })));
    renderAs("SUPERVISOR", <AlertDetailPage />, "/alerts/al1", "/alerts/:id");
    expect(await screen.findByText("False alarm")).toBeTruthy();
    for (const name of ["Resolve", "Escalate", "Dismiss", "Send message"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.getByText(/no new messages can be posted/)).toBeTruthy();
  });

  it("posts to the alert chat and shows the returned thread", async () => {
    const calls = mockApi([
      ...DETAIL(alert()),
      { method: "POST", url: /messages$/, status: 201, body: [
        { id: "m1", sender_role: "SUPERVISOR", sender_name: "Supervisor S", body: "Go to Main Gate", created_at: "2026-11-01T10:03:00Z" }] },
    ]);
    renderAs("SUPERVISOR", <AlertDetailPage />, "/alerts/al1", "/alerts/:id");
    await userEvent.type(await screen.findByLabelText(/Message/), "Go to Main Gate");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Go to Main Gate", { selector: ".chat-body" })).toBeTruthy();
    expect(sent(calls, "POST", /messages$/)[0].body).toEqual({ body: "Go to Main Gate", client_message_id: expect.any(String) });
  });

  it("splits Guard IDs on commas, spaces and new lines", () => {
    expect(parseGuardIds(" A1, B2\nC3;; ")).toEqual(["A1", "B2", "C3"]);
  });
});

describe("manual reports", () => {
  it("lists reports apart from alerts", async () => {
    const calls = mockApi([[/\/manual-reports\?/, [report()]]]);
    renderAs("SUPERVISOR", <ReportsPage />);
    expect((await screen.findByRole("link", { name: "Suspicious Person" })).getAttribute("href")).toBe("/manual-reports/r1");
    expect(screen.getByText(/They are not AI alerts/)).toBeTruthy();
    expect(calls[0].url).toBe("/api/v1/manual-reports?limit=26&offset=0");
  });

  it("marks a report as reviewed with a note, and never asks for a photo that does not exist", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/manual-reports\/r1$/, body: report() },
      { method: "POST", url: /review$/, body: report({ status: "REVIEWED" }) },
    ]);
    renderAs("ADMIN", <ReportDetailPage />, "/manual-reports/r1", "/manual-reports/:id");
    expect(await screen.findByText("The Guard attached no photo.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Mark as reviewed" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Review note/), "Patrol informed");
    fireEvent.click(within(dialog).getByRole("button", { name: "Mark as reviewed" }));
    await waitFor(() => expect(sent(calls, "POST", /review$/)).toHaveLength(1));
    expect(sent(calls, "POST", /review$/)[0].body).toEqual({ note: "Patrol informed" });
    expect(sent(calls, "GET", /photo$/)).toHaveLength(0);
  });

  it("shows the photo of a reviewed report without a review button", async () => {
    URL.createObjectURL = () => "blob:photo";
    URL.revokeObjectURL = () => undefined;
    const calls = mockApi([
      { method: "GET", url: /\/manual-reports\/r1$/, body: report({ has_photo: true, status: "REVIEWED", review_note: "Done", reviewed_at: "2026-11-01T12:00:00Z" }) },
      { method: "GET", url: /photo$/, body: {} },
    ]);
    renderAs("SUPERVISOR", <ReportDetailPage />, "/manual-reports/r1", "/manual-reports/:id");
    expect((await screen.findByRole("img")).getAttribute("src")).toBe("blob:photo");
    expect(sent(calls, "GET", /photo$/)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Mark as reviewed" })).toBeNull();
  });
});

describe("notifications", () => {
  const ITEMS = [
    { id: "n1", kind: "ALERT_NEW", title: "Weapon detected: Gate Camera", alert_id: "al1", manual_report_id: null, created_at: "2026-11-01T10:00:00Z", read_at: null },
    { id: "n2", kind: "MANUAL_REPORT_SUBMITTED", title: "Manual report: Suspicious Person", alert_id: null, manual_report_id: "r1", created_at: "2026-11-01T09:00:00Z", read_at: "2026-11-01T09:30:00Z" },
  ];

  it("lists, filters by type, and marks one or all as read", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/notifications\?/, body: ITEMS },
      { method: "POST", url: /\/n1\/read$/, body: null, status: 204 },
      { method: "POST", url: /read-all$/, body: { marked_read: 1 } },
    ]);
    renderAs("SUPERVISOR", <NotificationsPage />);
    expect(await screen.findByText("Weapon detected: Gate Camera")).toBeTruthy();
    expect(screen.getByText("Unread")).toBeTruthy();
    expect(calls[0].url).toBe("/api/v1/notifications?unread_only=false&limit=100");

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "MANUAL_REPORT_SUBMITTED" } });
    expect(screen.queryByText("Weapon detected: Gate Camera")).toBeNull();
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "" } });

    fireEvent.click(screen.getByRole("button", { name: "Mark as read" }));
    await waitFor(() => expect(sent(calls, "POST", /\/n1\/read$/)).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Mark all as read" }));
    expect(await screen.findByText("1 notification marked as read.")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Unread only"));
    await waitFor(() => expect(sent(calls, "GET", /unread_only=true/)).toHaveLength(1));
  });

  it("opens the linked alert and marks the notification as read", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/notifications\?/, body: ITEMS },
      { method: "POST", url: /\/n1\/read$/, body: null, status: 204 },
    ]);
    renderAs("ADMIN", <NotificationsPage />, "/notifications", "/notifications");
    fireEvent.click(await screen.findByRole("button", { name: "Open alert" }));
    await screen.findByText("navigated away");
    expect(sent(calls, "POST", /\/n1\/read$/)).toHaveLength(1);
  });

  it("shows the unread count in the topbar link", async () => {
    const calls = mockApi([[/\/notifications\?unread_only=true/, [ITEMS[0]]]]);
    renderAs("SUPERVISOR", <NotificationBell />);
    expect(await screen.findByRole("link", { name: "Notifications, 1 unread" })).toBeTruthy();
    expect(calls[0].url).toBe("/api/v1/notifications?unread_only=true&limit=100");
  });
});
