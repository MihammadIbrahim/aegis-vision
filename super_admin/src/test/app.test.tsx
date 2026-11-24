// Platform Super Admin web app. Mocked URLs, methods and bodies are the Core API's (platform routes,
// decision D-019), so these tests also pin the contract.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getToken, setToken, TOKEN_KEY } from "../api/client";
import type { AiSettings, AuditEntry, Health, Organization, OrganizationDetail, Overview } from "../api/types";
import { App } from "../app/App";
import { AuthProvider } from "../auth/AuthContext";
import { loginMethodOf } from "../auth/token";
import { AppShell } from "../app/layouts/AppShell";
import { ToastProvider } from "../components/ui";
import { detailText } from "../components/common/AuditTable";
import { AiSettingsPage, aiProblems } from "../features/ai-settings/AiSettingsPage";
import { AuditLogsPage } from "../features/audit-logs/AuditLogsPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { HealthPage } from "../features/health/HealthPage";
import { OrganizationDetailPage } from "../features/organizations/OrganizationDetailPage";
import { OrganizationsPage } from "../features/organizations/OrganizationsPage";
import { passwordProblem, ProfilePage } from "../features/profile/ProfilePage";
import { mockApi, renderAs, sent } from "./helpers";

/** A fake platform token whose payload carries the login method ("lm"). */
function token(lm: "NORMAL" | "RECOVERY" = "NORMAL", tag = "x"): string {
  const payload = btoa(JSON.stringify({ sub: "A1", aud: "platform", lm, ver: 1, tag })).replace(/=+$/, "");
  return `h.${payload}.s`;
}
const TOKEN = (lm: "NORMAL" | "RECOVERY" = "NORMAL", tag = "x") => ({ access_token: token(lm, tag), token_type: "bearer", expires_in: 1800 });
const ME = { id: "A1", username: "platform.root", role: "PLATFORM_SUPER_ADMIN", created_at: "2026-10-27T08:00:00Z", last_login_at: null };

const ORG: Organization = {
  organization_id: "ORG-00000000001", name: "Riverside University", official_email: "info@riverside.example.com",
  official_phone_number: "+923001234567", status: "ACTIVE", created_at: "2026-10-27T09:00:00Z",
  main_admin: { full_name: "Hina Malik", username: "hina.m", email: "hina@riverside.example.com", phone_number: "+923001112222" },
  campus_count: 2, camera_count: 5,
};
const DETAIL: OrganizationDetail = {
  ...ORG, registered_address: null,
  campuses: [{ name: "Main Campus", status: "ACTIVE", created_at: "2026-10-27T09:00:00Z", building_count: 3, perimeter_count: 1,
    floor_count: 4, area_count: 9, camera_count: 5, admin_count: 2, supervisor_count: 1, guard_count: 6 }],
};
const ENTRY: AuditEntry = {
  id: "e1", occurred_at: "2026-11-12T10:00:00Z", actor_type: "SUPER_ADMIN", actor_username: "platform.root",
  action: "ORGANIZATION_STATUS_CHANGED", outcome: "SUCCESS", organization_id: "ORG-00000000001",
  organization_name: "Riverside University", ip_address: "127.0.0.1", user_agent: "tests",
  details: { from_status: "ACTIVE", to_status: "SUSPENDED", organization_code: "ORG-00000000001" },
};
const AI: AiSettings = {
  human_detection_tracking_enabled: true, person_reid_enabled: true, weapon_detection_enabled: true,
  fight_violence_detection_enabled: true, restricted_area_face_recognition_enabled: false, rag_assistant_enabled: false,
  agentic_investigation_enabled: false, default_confidence_threshold: "0.50", frame_sample_fps: 5,
  detection_cooldown_seconds: 60, max_task_retries: 3, task_timeout_seconds: 30,
  modules: [
    { key: "human_detection_tracking_enabled", enabled: true, implemented: true },
    { key: "person_reid_enabled", enabled: true, implemented: false },
    { key: "weapon_detection_enabled", enabled: true, implemented: false },
    { key: "fight_violence_detection_enabled", enabled: true, implemented: false },
    { key: "restricted_area_face_recognition_enabled", enabled: false, implemented: false },
    { key: "rag_assistant_enabled", enabled: false, implemented: false },
    { key: "agentic_investigation_enabled", enabled: false, implemented: false },
  ],
  updated_at: "2026-11-12T08:00:00Z", updated_by_username: null,
};
const HEALTH: Health = {
  checked_at: "2026-11-12T10:00:00Z", overall_status: "OPERATIONAL", summary: "All deployed services operational (4 planned services not deployed)",
  services: [
    { key: "core_api", name: "Core API (FastAPI)", status: "OPERATIONAL", latency_ms: 2.5, detail: "Answering requests", metrics: { uptime_seconds: 7500 } },
    { key: "database", name: "Database (PostgreSQL)", status: "OPERATIONAL", latency_ms: 0.8, detail: "PostgreSQL 16.4",
      metrics: { server_version: "16.4", database_size_bytes: 10485760, extensions: ["plpgsql"], pgvector_installed: false, connection_pool: { checkedout: 1 } } },
    { key: "media_storage", name: "Media storage (local disk)", status: "OPERATIONAL", latency_ms: null, detail: "Campus media folder", metrics: { used_percent: 85, writable: true } },
    { key: "message_broker", name: "Message Broker (RabbitMQ)", status: "NOT_DEPLOYED", latency_ms: null, detail: "Not deployed.", metrics: {} },
  ],
  storage: { path: "/mnt/cctv_storage", accessible: true, writable: true, total_bytes: 1000, used_bytes: 850, free_bytes: 150,
    used_percent: 85, level: "WARNING", warning_percent: 80, critical_percent: 90 },
  problems: ["Storage is 85% full (warning at 80%)."],
};
const OVERVIEW: Overview = {
  counts: { organizations_total: 3, organizations_active: 2, organizations_inactive: 0, organizations_suspended: 1,
    deleted_organizations_total: 4, campuses_total: 5, campuses_active: 5, cameras_total: 12, admins_active: 3,
    supervisors_active: 2, guards_active: 7 },
  health: { overall_status: "DEGRADED", summary: "1 service degraded or down (4 planned services not deployed)" },
  ai_modules: AI.modules,
  recent_activity: [ENTRY],
};

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() }));
});
afterEach(() => setToken(null));

function app(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("sign-in and session", () => {
  it("signs in with username and password only, stores the token under its own key, and opens the dashboard", async () => {
    const calls = mockApi([
      { method: "POST", url: /\/platform\/auth\/login$/, body: TOKEN() },
      { method: "GET", url: /\/platform\/me$/, body: ME },
      { method: "GET", url: /\/platform\/overview$/, body: OVERVIEW },
    ]);
    sessionStorage.setItem("cctv.accessToken", "organization-token");
    app("/organizations");
    // No email code, sign-up or forgot-password: the page has no links and only two fields.
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.queryByLabelText(/code|otp/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /sign up|register|forgot/i })).toBeNull();
    const password = screen.getByLabelText(/^Password/) as HTMLInputElement;
    expect(password.type).toBe("password");
    fireEvent.click(screen.getByLabelText(/Show password/));
    expect(password.type).toBe("text");
    fireEvent.change(screen.getByLabelText(/^Username/), { target: { value: " platform.root " } });
    fireEvent.change(password, { target: { value: "Secret-pass-2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    // Sign-in, /me, navigation and the overview chain several requests: allow time on a busy machine.
    expect(await screen.findByRole("heading", { name: "Platform overview" }, { timeout: 5000 })).toBeTruthy();
    expect(sent(calls, "POST", /\/auth\/login$/)[0].body).toEqual({ username: "platform.root", password: "Secret-pass-2026" });
    expect(TOKEN_KEY).toBe("cctv.platform.accessToken");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe(token());
    expect(sessionStorage.getItem("cctv.accessToken")).toBe("organization-token"); // the other app's session is untouched
  });

  it("shows invalid credentials and the lockout distinctly", async () => {
    mockApi([{ method: "POST", url: /\/auth\/login$/, status: 401, body: { detail: "Invalid username or password." } }]);
    app();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    // The sign-in notice (the toast region is also an alert region).
    const notice = () => document.querySelector(".login .notice[role=alert]");
    await waitFor(() => expect(notice()?.textContent).toBe("Enter your username and password."));
    fireEvent.change(screen.getByLabelText(/^Username/), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "y" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(notice()?.textContent).toBe("Invalid username or password."));
    mockApi([{ method: "POST", url: /\/auth\/login$/, status: 429, body: { detail: "Account temporarily locked." } }]);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(notice()?.textContent).toMatch(/locked/));
    expect(notice()?.className).toContain("warning");
  });

  it("reads the login method from the token and signs out on a 401", async () => {
    expect(loginMethodOf(token("RECOVERY"))).toBe("RECOVERY");
    expect(loginMethodOf("garbage")).toBe("NORMAL");
    setToken(token());
    mockApi([
      { method: "GET", url: /\/platform\/me$/, body: ME },
      { method: "GET", url: /\/platform\/overview$/, status: 401, body: { detail: "Session expired. Please log in again." } },
    ]);
    app();
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(getToken()).toBeNull();
  });

  it("surfaces the emergency recovery flow in a recovery session", () => {
    renderAs("RECOVERY", <AppShell>page</AppShell>);
    expect(screen.getByText("Emergency session")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Restore the normal username and password" }).getAttribute("href")).toBe("/profile#recovery");
    for (const name of ["Dashboard", "Organizations", "AI Settings", "System Health", "Audit Logs", "Profile & Security"]) {
      expect(screen.getByRole("link", { name })).toBeTruthy();
    }
  });
});

describe("Dashboard", () => {
  it("shows counts, health, AI modules and recent activity", async () => {
    mockApi([{ method: "GET", url: /\/platform\/overview$/, body: OVERVIEW }]);
    renderAs("NORMAL", <DashboardPage />);
    expect(await screen.findByRole("heading", { name: "Platform overview" })).toBeTruthy();
    const tile = (label: string) => screen.getByText(label, { selector: ".stat-label" }).closest(".stat-tile")!.querySelector(".stat-value")!.textContent;
    expect([tile("Organizations"), tile("Suspended"), tile("Deleted"), tile("Registered cameras"), tile("Active staff")]).toEqual(["3", "1", "4", "12", "12"]);
    expect(screen.getByText("Degraded")).toBeTruthy();
    expect(screen.getAllByText("Not built yet")).toHaveLength(6);
    expect(within(screen.getByRole("table", { name: "Recent platform activity" })).getByText("Organization status changed")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Quick actions" })).toBeTruthy();
  });

  it("shows an error with retry", async () => {
    mockApi([{ method: "GET", url: /\/overview$/, status: 503, body: { detail: "Service unavailable." } }]);
    renderAs("NORMAL", <DashboardPage />);
    expect(await screen.findByText("Service unavailable.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("Organizations", () => {
  it("lists with search, status, sort, paging, export and deleted names", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/platform\/organizations\?/, body: { items: [ORG], total: 30, limit: 25, offset: 0 } },
      { method: "GET", url: /\/platform\/organizations\/export/, body: "csv" },
      { method: "GET", url: /\/platform\/deleted-organizations$/, body: { organization_names: ["Old Logistics"] } },
    ]);
    renderAs("NORMAL", <OrganizationsPage />);
    const table = await screen.findByRole("table", { name: "Organizations" });
    expect(within(table).getByRole("link", { name: "Riverside University" }).getAttribute("href")).toBe("/organizations/ORG-00000000001");
    expect(within(table).getByText("hina.m")).toBeTruthy();
    expect(calls[0].url).toBe("/api/v1/platform/organizations?sort=created_at&order=desc&limit=25&offset=0");
    expect(screen.getByText("1–25 of 30")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(calls.at(-1)!.url).toContain("offset=25"));
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "river" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "SUSPENDED" } });
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "camera_count" } });
    fireEvent.change(screen.getByLabelText("Order"), { target: { value: "asc" } });
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/v1/platform/organizations?q=river&status=SUSPENDED&sort=camera_count&order=asc&limit=25&offset=0")).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(sent(calls, "GET", /\/export/)).toHaveLength(1));
    expect(sent(calls, "GET", /\/export/)[0].url).toBe("/api/v1/platform/organizations/export?q=river&status=SUSPENDED&sort=camera_count&order=asc");
    expect(await screen.findByText("Organization directory exported.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(await screen.findByText("Old Logistics")).toBeTruthy();
  });

  it("says plainly when nothing matches", async () => {
    mockApi([{ method: "GET", url: /\/organizations\?/, body: { items: [], total: 0, limit: 25, offset: 0 } }]);
    renderAs("NORMAL", <OrganizationsPage />);
    expect(await screen.findByText("No organizations yet.")).toBeTruthy();
  });
});

describe("Organization details", () => {
  const detailRoute = { method: "GET", url: /\/platform\/organizations\/ORG-00000000001$/, body: DETAIL };

  it("shows the profile, Main Admin and campus counts, never media", async () => {
    mockApi([detailRoute]);
    renderAs("NORMAL", <OrganizationDetailPage />, "/organizations/ORG-00000000001", "/organizations/:organizationId");
    expect(await screen.findByRole("heading", { name: "Riverside University", level: 1 })).toBeTruthy();
    expect(screen.getByText("Not collected at registration")).toBeTruthy();
    const campuses = screen.getByRole("table", { name: "Campuses" });
    expect(within(campuses).getByText("Main Campus")).toBeTruthy();
    expect(within(campuses).getByText("2 Admins · 1 Supervisors · 6 Guards")).toBeTruthy();
    expect(screen.queryByText(/stream|rtsp|recording url/i)).toBeNull();
    expect(screen.getByText("Passwords are never visible to the Super Admin.")).toBeTruthy();
  });

  it("edits the Main Admin with the current password and shows a refusal in the dialog", async () => {
    const calls = mockApi([
      detailRoute,
      { method: "PUT", url: /\/main-admin$/, status: 403, body: { detail: "The current password is incorrect." } },
    ]);
    renderAs("NORMAL", <OrganizationDetailPage />, "/organizations/ORG-00000000001", "/organizations/:organizationId");
    fireEvent.click(await screen.findByRole("button", { name: "Edit contact" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Main Admin of Riverside University" });
    const save = within(dialog).getByRole("button", { name: "Save contact details" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/^Email/), { target: { value: " new@riverside.example.com " } });
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "wrong" } });
    fireEvent.click(save);
    expect(await within(dialog).findByText("The current password is incorrect.")).toBeTruthy();
    expect(sent(calls, "PUT", /\/main-admin$/)[0].body).toEqual({
      full_name: "Hina Malik", email: "new@riverside.example.com", phone_number: "+923001112222", current_password: "wrong",
    });
  });

  it("suspends with a reason and the current password", async () => {
    const calls = mockApi([detailRoute, { method: "POST", url: /\/status$/, body: { ...ORG, status: "SUSPENDED" } }]);
    renderAs("NORMAL", <OrganizationDetailPage />, "/organizations/ORG-00000000001", "/organizations/:organizationId");
    fireEvent.click(await screen.findByRole("button", { name: "Change status" }));
    const dialog = screen.getByRole("dialog", { name: "Change the status of Riverside University" });
    expect(within(dialog).queryByLabelText(/^Activate/)).toBeNull(); // already active
    fireEvent.click(within(dialog).getByLabelText(/^Suspend/));
    fireEvent.change(within(dialog).getByLabelText(/^Reason/), { target: { value: "Unpaid invoice" } });
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Secret-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Suspend organization" }));
    await waitFor(() => expect(sent(calls, "POST", /\/status$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/status$/)[0].body).toEqual({ status: "SUSPENDED", reason: "Unpaid invoice", current_password: "Secret-pass-2026" });
    expect(await screen.findByText("Riverside University is now suspended.")).toBeTruthy();
  });

  it("reports an unknown organization", async () => {
    mockApi([{ method: "GET", url: /\/organizations\/ORG-0$/, status: 404, body: { detail: "Organization not found.", code: "ORGANIZATION_NOT_FOUND" } }]);
    renderAs("NORMAL", <OrganizationDetailPage />, "/organizations/ORG-0", "/organizations/:organizationId");
    expect(await screen.findByText("Organization not found.")).toBeTruthy();
  });
});

describe("AI settings", () => {
  it("marks unbuilt modules, validates parameters and saves every value with the password", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/platform\/ai-settings$/, body: AI },
      { method: "PUT", url: /\/platform\/ai-settings$/, body: { ...AI, rag_assistant_enabled: true, frame_sample_fps: 8 } },
    ]);
    renderAs("NORMAL", <AiSettingsPage />);
    expect(await screen.findByRole("heading", { name: "Platform AI settings" })).toBeTruthy();
    expect(screen.getAllByText("Not built yet")).toHaveLength(6);
    const save = screen.getByRole("button", { name: "Save settings" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed
    fireEvent.click(screen.getByRole("switch", { name: "RAG security assistant" }));
    fireEvent.change(screen.getByLabelText(/^Frame sampling/), { target: { value: "99" } });
    expect(screen.getByText("Enter a whole number from 1 to 30.")).toBeTruthy();
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^Frame sampling/), { target: { value: "8" } });
    fireEvent.click(save);
    const dialog = screen.getByRole("dialog", { name: "Save platform AI settings" });
    expect(within(dialog).getByText(/2 settings will change/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Secret-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(sent(calls, "PUT", /ai-settings$/)).toHaveLength(1));
    const { modules: _m, updated_at: _u, updated_by_username: _b, ...values } = AI;
    expect(sent(calls, "PUT", /ai-settings$/)[0].body).toEqual({ ...values, rag_assistant_enabled: true, frame_sample_fps: 8, current_password: "Secret-pass-2026" });
  });

  it("checks the threshold format", () => {
    const { modules: _m, updated_at: _u, updated_by_username: _b, ...values } = AI;
    expect(aiProblems(values)).toEqual({});
    expect(Object.keys(aiProblems({ ...values, default_confidence_threshold: "1.5" }))).toEqual(["default_confidence_threshold"]);
    expect(Object.keys(aiProblems({ ...values, default_confidence_threshold: "0.555" }))).toEqual(["default_confidence_threshold"]);
  });
});

describe("System health", () => {
  it("shows measured services, honest NOT_DEPLOYED services, storage level and refreshes", async () => {
    const calls = mockApi([{ method: "GET", url: /\/platform\/health$/, body: HEALTH }]);
    renderAs("NORMAL", <HealthPage />);
    expect(await screen.findByText(HEALTH.summary)).toBeTruthy();
    expect(screen.getByText("Not deployed")).toBeTruthy();
    expect(screen.getByText("Not installed")).toBeTruthy(); // pgvector
    expect(screen.getByText("2 h 5 min")).toBeTruthy();
    expect(screen.getByRole("meter", { name: "Storage used" }).getAttribute("aria-valuenow")).toBe("85");
    expect(screen.getAllByText("Warning").length).toBeGreaterThan(0);
    expect(screen.getByText("Storage is 85% full (warning at 80%).")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(sent(calls, "GET", /\/health$/)).toHaveLength(2));
  });
});

describe("Audit logs", () => {
  it("filters, pages and exports with the applied filters; never shows secret details", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/platform\/audit-logs\/actions$/, body: [{ action: "ORGANIZATION_STATUS_CHANGED", count: 4 }] },
      { method: "GET", url: /\/platform\/audit-logs\/export/, body: "csv" },
      { method: "GET", url: /\/platform\/audit-logs\?/, body: { items: [{ ...ENTRY, details: { ...ENTRY.details, password: "leak", reason: "Unpaid" } }], total: 1, limit: 50, offset: 0 } },
    ]);
    renderAs("NORMAL", <AuditLogsPage />);
    const table = await screen.findByRole("table", { name: "Platform audit log" });
    expect(within(table).queryByText(/leak/)).toBeNull();
    expect(within(table).getByText(/To status: SUSPENDED/)).toBeTruthy();
    expect(within(table).getByText("tests")).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("option", { name: "Organization status changed (4)" })).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Organization"), { target: { value: "riverside" } });
    fireEvent.change(screen.getByLabelText("Action"), { target: { value: "ORGANIZATION_STATUS_CHANGED" } });
    fireEvent.change(screen.getByLabelText("Outcome"), { target: { value: "FAILURE" } });
    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-11-12" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-11-01" } });
    expect(screen.getByText("The end date is before the start date.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Apply" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-11-30" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    const filtered = "organization=riverside&action=ORGANIZATION_STATUS_CHANGED&outcome=FAILURE&date_from=2026-11-12&date_to=2026-11-30";
    await waitFor(() => expect(calls.some((c) => c.url === `/api/v1/platform/audit-logs?${filtered}&limit=50&offset=0`)).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(sent(calls, "GET", /\/export/)).toHaveLength(1));
    expect(sent(calls, "GET", /\/export/)[0].url).toBe(`/api/v1/platform/audit-logs/export?${filtered}`);
  });

  it("hides secret-looking keys in details", () => {
    expect(detailText({ password: "x", access_token: "y", otp: "1", fields: ["email"], organization_code: "ORG" })).toBe('Fields: ["email"]');
  });
});

describe("Profile & security", () => {
  it("changes the username and stores the fresh token (rotation)", async () => {
    const calls = mockApi([{ method: "POST", url: /\/platform\/me\/username$/, body: TOKEN("NORMAL", "rotated") }]);
    const seen: string[] = [];
    renderAs("NORMAL", <ProfilePage />, "/", undefined, (t) => seen.push(t));
    fireEvent.change(screen.getByLabelText(/^New username/), { target: { value: "bad name" } });
    expect(screen.getByText("Not a valid username.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/^New username/), { target: { value: "root.admin" } });
    fireEvent.change(screen.getAllByLabelText(/^Current password/)[0], { target: { value: "Secret-pass-2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Save username" }));
    await waitFor(() => expect(seen).toEqual([token("NORMAL", "rotated")]));
    expect(getToken()).toBe(token("NORMAL", "rotated"));
    expect(sent(calls, "POST", /username$/)[0].body).toEqual({ current_password: "Secret-pass-2026", new_username: "root.admin" });
    expect(await screen.findByText("Username changed.")).toBeTruthy();
  });

  it("enforces the password policy and confirmation, then rotates the token", async () => {
    const calls = mockApi([{ method: "POST", url: /\/platform\/me\/password$/, body: TOKEN("NORMAL", "pw") }]);
    const seen: string[] = [];
    renderAs("NORMAL", <ProfilePage />, "/", undefined, (t) => seen.push(t));
    const update = screen.getByRole("button", { name: "Update password" }) as HTMLButtonElement;
    fireEvent.change(screen.getAllByLabelText(/^Current password/)[1], { target: { value: "Old-pass-2026x" } });
    fireEvent.change(screen.getByLabelText(/^New password/), { target: { value: "short1" } });
    expect(screen.getByText("Use 12 to 128 characters.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/^New password/), { target: { value: "platform.root-2027" } });
    expect(screen.getByText("The password must not contain the username.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/^New password/), { target: { value: "Fresh-pass-2027" } });
    fireEvent.change(screen.getByLabelText(/^Confirm new password/), { target: { value: "Fresh-pass-2028" } });
    expect(screen.getByText("The passwords do not match.")).toBeTruthy();
    expect(update.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^Confirm new password/), { target: { value: "Fresh-pass-2027" } });
    fireEvent.click(update);
    await waitFor(() => expect(seen).toEqual([token("NORMAL", "pw")]));
    expect(sent(calls, "POST", /password$/)[0].body).toEqual({ current_password: "Old-pass-2026x", new_password: "Fresh-pass-2027", confirm_new_password: "Fresh-pass-2027" });
    expect(passwordProblem("onlyletterslong", "x")).toBe("Use at least one letter and one digit.");
  });

  it("shows the lockout and wrong-password replies", async () => {
    mockApi([{ method: "POST", url: /username$/, status: 429, body: { detail: "Account temporarily locked." } }]);
    renderAs("NORMAL", <ProfilePage />);
    fireEvent.change(screen.getByLabelText(/^New username/), { target: { value: "root.admin" } });
    fireEvent.change(screen.getAllByLabelText(/^Current password/)[0], { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save username" }));
    expect(await screen.findByText(/locked for a while/)).toBeTruthy();
  });

  it("offers emergency recovery only in a recovery session, with the exact body", async () => {
    const { unmount } = renderAs("NORMAL", <ProfilePage />);
    expect(screen.getByText(/Available only in a session opened with the emergency recovery credentials/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Restore normal credentials" })).toBeNull();
    unmount();
    const calls = mockApi([{ method: "POST", url: /\/platform\/recovery\/credentials$/, body: TOKEN("RECOVERY", "restored") }]);
    const seen: string[] = [];
    renderAs("RECOVERY", <ProfilePage />, "/", undefined, (t) => seen.push(t));
    fireEvent.change(screen.getByLabelText(/^Recovery username/), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText(/^Recovery password/), { target: { value: "admin@123" } });
    const card = screen.getByRole("heading", { name: /Emergency credential recovery/ }).closest("section")!;
    fireEvent.change(within(card).getByLabelText(/^New username/), { target: { value: "restored.admin" } });
    fireEvent.change(within(card).getByLabelText(/^New password/), { target: { value: "Brand-new-pass-2027" } });
    fireEvent.change(within(card).getByLabelText(/^Confirm new password/), { target: { value: "Brand-new-pass-2027" } });
    fireEvent.click(within(card).getByRole("button", { name: "Restore normal credentials" }));
    await waitFor(() => expect(seen).toEqual([token("RECOVERY", "restored")]));
    expect(sent(calls, "POST", /recovery\/credentials$/)[0].body).toEqual({
      recovery_username: "admin", recovery_password: "admin@123", new_username: "restored.admin",
      new_password: "Brand-new-pass-2027", confirm_new_password: "Brand-new-pass-2027",
    });
  });
});
