// Phase 8: Staff Management. The mocked URLs, methods and bodies are the Core API's (staff_router.py,
// guard_router.py, supervisor_router.py, location_router.py), so these tests also pin the contract.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { canAdministerStaff, canManageStaff } from "../auth/permissions";
import { NotificationsPage } from "../features/notifications/pages/NotificationsPage";
import type { StaffMember } from "../features/staff";
import { StaffMemberPage } from "../features/staff/pages/StaffMemberPage";
import { StaffPage } from "../features/staff/pages/StaffPage";
import { formProblems, passwordProblem } from "../features/staff/validation";
import { mockApi, renderAs, sent } from "./helpers";

const GUARD_ID = "ABCDEF123456789012345678901";
const PATH = { level: "AREA", building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR", floor_id: "f1", floor_name: "Ground", area_id: "a1", area_name: "Gate" };

function member(over: Partial<StaffMember> = {}): StaffMember {
  return {
    id: "u1", display_id: GUARD_ID, role: "GUARD", status: "ACTIVE", full_name: "Bilal Guard", username: "bilal.g",
    email: "bilal@acme.example", phone_number: "+923001234567", created_at: "2026-11-01T10:00:00Z",
    category: "STANDBY", location: null, ...over,
  };
}

const STATIONED = member({ id: "u2", display_id: "GHIJKL123456789012345678901", full_name: "Hamza Guard", category: "STATIONED", location: PATH as StaffMember["location"] });
const SUPERVISOR = member({ id: "s1", display_id: "SUPABC123456789012345678901", role: "SUPERVISOR", full_name: "Sana Supervisor", category: null });
const TREE = [{ id: "b1", name: "Admin Block", kind: "INDOOR", camera_count: 0, guard_count: 1,
  floors: [{ id: "f1", building_id: "b1", name: "Ground", level: 0, camera_count: 0, guard_count: 1,
    areas: [{ id: "a1", building_id: "b1", floor_id: "f1", name: "Gate", boundary_direction: null, boundary_name: null, boundary_side: null, camera_count: 0, guard_count: 1 }] }],
  areas: [] }];

const CODE = (id: string, email = "ma***@acme.example", resend = 0) => ({
  id, status: "OTP_REQUIRED", email, delivery: "email", otp_expires_in_seconds: 300, resend_available_in_seconds: resend, expires_at: "2026-11-11T10:10:00Z",
});

describe("staff permissions", () => {
  it("lets Admins and Supervisors manage Guards, and only Admins administer staff", () => {
    expect(canManageStaff("ADMIN")).toBe(true);
    expect(canManageStaff("SUPERVISOR")).toBe(true);
    expect(canManageStaff("GUARD")).toBe(false);
    expect(canAdministerStaff("ADMIN")).toBe(true);
    expect(canAdministerStaff("SUPERVISOR")).toBe(false);
  });

  it("checks the creation form like the server", () => {
    expect(passwordProblem("short1", "u", "e@x.io")).toMatch(/12 to 128/);
    expect(passwordProblem("onlyletters-here", "u1", "e@x.io")).toMatch(/one letter and one digit/);
    expect(passwordProblem("bilal.g-2026-strong", "bilal.g", "e@x.io")).toMatch(/username/);
    expect(passwordProblem("Strong-pass-2026", "bilal.g", "e@x.io")).toBeUndefined();
    expect(Object.keys(formProblems({ full_name: "", username: "a", email: "x", phone_number: "12", password: "" }))).toEqual([
      "full_name", "username", "email", "phone_number", "password",
    ]);
    expect(formProblems({ full_name: "Bilal Guard", username: "bilal.g", email: "b@acme.example", phone_number: "+92 300 1234567", password: "Strong-pass-2026" })).toEqual({});
  });
});

describe("Staff list", () => {
  it("shows Admins Guards with their category and post, and Supervisors on a second tab", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/staff\?role=GUARD&status=ACTIVE&limit=26&offset=0$/, body: [member(), STATIONED] },
      { method: "GET", url: /\/staff\?role=SUPERVISOR&status=ACTIVE&limit=26&offset=0$/, body: [SUPERVISOR] },
    ]);
    renderAs("ADMIN", <StaffPage />, "/staff");
    const table = await screen.findByRole("table", { name: "Guards" });
    expect(within(table).getByRole("link", { name: "Bilal Guard" }).getAttribute("href")).toBe("/staff/u1");
    expect(within(table).getByText("Standby")).toBeTruthy();
    expect(within(table).getByText("Stationed")).toBeTruthy();
    expect(within(table).getByText("Admin Block › Ground › Gate")).toBeTruthy();
    expect(within(table).getByText("Campus-wide (no post)")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add Guard" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /Supervisors/ }));
    expect(await screen.findByText("Sana Supervisor")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add Supervisor" })).toBeTruthy();
    expect(screen.queryByLabelText("Category")).toBeNull();
    expect(sent(calls, "GET", /role=SUPERVISOR/)).toHaveLength(1);
  });

  it("gives Supervisors Guards only: no tabs, no Add button, no Supervisor request", async () => {
    const calls = mockApi([{ method: "GET", url: /\/staff\?role=GUARD/, body: [member()] }]);
    renderAs("SUPERVISOR", <StaffPage />, "/staff?role=SUPERVISOR");
    expect(await screen.findByText("Bilal Guard")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Add / })).toBeNull();
    expect(calls.some((c) => c.url.includes("role=SUPERVISOR"))).toBe(false);
    expect(screen.getByText(/only Organization Admins add or deactivate Guards/)).toBeTruthy();
  });

  it("filters by category and status and searches as you type", async () => {
    const calls = mockApi([{ method: "GET", url: /\/staff\?/, body: [] }]);
    renderAs("SUPERVISOR", <StaffPage />, "/staff");
    expect(await screen.findByText("No active Guards yet.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "STANDBY" } });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/staff?role=GUARD&status=ACTIVE&category=STANDBY&limit=26&offset=0"))).toBe(true));
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "LEFT" } });
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "bilal" } });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/staff?role=GUARD&q=bilal&status=LEFT&category=STANDBY&limit=26&offset=0"))).toBe(true));
    expect(await screen.findByText("No Guards match.")).toBeTruthy();
  });
});

describe("Add Guard / Supervisor wizard (Admins)", () => {
  async function openWizard(role: "GUARD" | "SUPERVISOR", extra: Parameters<typeof mockApi>[0] = []) {
    const calls = mockApi([
      ...extra,
      { method: "GET", url: /\/staff\?/, body: [] },
    ]);
    renderAs("ADMIN", <StaffPage />, role === "SUPERVISOR" ? "/staff?role=SUPERVISOR" : "/staff");
    fireEvent.click(await screen.findByRole("button", { name: role === "GUARD" ? "Add Guard" : "Add Supervisor" }));
    return { calls, dialog: screen.getByRole("dialog", { name: role === "GUARD" ? "Add Guard" : "Add Supervisor" }) };
  }

  it("runs the four steps for a Guard with the exact requests and shows the 27-character ID", async () => {
    const { calls, dialog } = await openWizard("GUARD", [
      { method: "POST", url: /\/guards\/authorization$/, body: CODE("a1") },
      { method: "POST", url: /\/guards\/authorization\/a1\/verify$/, body: { authorization_id: "a1", status: "VERIFIED", form_expires_at: "2026-11-11T10:20:00Z" } },
      { method: "POST", url: /\/guards$/, status: 201, body: CODE("c1", "bi***@acme.example") },
      { method: "POST", url: /\/guards\/creations\/c1\/verify$/, status: 201, body: {
        guard_id: GUARD_ID, full_name: "Bilal Guard", username: "bilal.g", email: "bilal@acme.example", phone_number: "+923001234567",
        role: "GUARD", organization_id: "ORG", campus_name: "Main Campus", created_at: "2026-11-11T10:05:00Z" } },
    ]);
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Admin-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send me a code" }));
    expect(await within(dialog).findByText(/ma\*\*\*@acme\.example/)).toBeTruthy();
    expect(sent(calls, "POST", /\/guards\/authorization$/)[0].body).toEqual({ current_password: "Admin-pass-2026" });
    fireEvent.change(within(dialog).getByLabelText(/^6-digit code/), { target: { value: "12a3456" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(sent(calls, "POST", /\/authorization\/a1\/verify$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/authorization\/a1\/verify$/)[0].body).toEqual({ otp: "123456" });

    const send = await within(dialog).findByRole("button", { name: "Send code to the Guard" });
    fireEvent.click(send);
    expect(within(dialog).getByText("Enter the full name.")).toBeTruthy();
    expect(sent(calls, "POST", /\/guards$/)).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText(/^Full name/), { target: { value: " Bilal Guard " } });
    fireEvent.change(within(dialog).getByLabelText(/^Username/), { target: { value: "bilal.g" } });
    fireEvent.change(within(dialog).getByLabelText(/^Email/), { target: { value: "bilal@acme.example" } });
    fireEvent.change(within(dialog).getByLabelText(/^Phone number/), { target: { value: "+92 300 1234567" } });
    fireEvent.change(within(dialog).getByLabelText(/^Initial password/), { target: { value: "bilal.g-2026-x" } });
    expect(within(dialog).getByText("The password must not contain the username.")).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^Initial password/), { target: { value: "Strong-pass-2026" } });
    fireEvent.click(send);
    await waitFor(() => expect(sent(calls, "POST", /\/guards$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/guards$/)[0].body).toEqual({
      authorization_id: "a1", full_name: "Bilal Guard", username: "bilal.g", email: "bilal@acme.example", phone_number: "+92 300 1234567", password: "Strong-pass-2026",
    });
    expect(await within(dialog).findByText(/bi\*\*\*@acme\.example/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^6-digit code/), { target: { value: "654321" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verify" }));
    expect(await within(dialog).findByText(GUARD_ID)).toBeTruthy();
    expect(GUARD_ID).toHaveLength(27);
    expect(within(dialog).getByText(/Standby Guard until you assign a location/)).toBeTruthy();
    expect(sent(calls, "POST", /\/creations\/c1\/verify$/)[0].body).toEqual({ otp: "654321" });
    expect(await screen.findByText("Bilal Guard added.")).toBeTruthy();
    expect(sent(calls, "GET", /\/staff\?/).length).toBeGreaterThanOrEqual(2);
  });

  it("creates Supervisors through /organization-admin/supervisors", async () => {
    const { calls, dialog } = await openWizard("SUPERVISOR", [
      { method: "POST", url: /\/organization-admin\/supervisors\/authorization$/, body: CODE("a2") },
    ]);
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Admin-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send me a code" }));
    await waitFor(() => expect(sent(calls, "POST", /\/organization-admin\/supervisors\/authorization$/)).toHaveLength(1));
    expect(calls.some((c) => c.url.includes("/guards"))).toBe(false);
  });

  it("shows refusals: wrong password, wrong code, taken username; resend waits for the cooldown", async () => {
    const { dialog } = await openWizard("GUARD", [
      { method: "POST", url: /\/guards\/authorization$/, status: 403, body: { detail: "The current password is incorrect.", code: "CURRENT_PASSWORD_INCORRECT" } },
    ]);
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "wrong" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send me a code" }));
    expect(await within(dialog).findByText("The current password is incorrect.")).toBeTruthy();

    mockApi([
      { method: "POST", url: /\/guards\/authorization$/, body: CODE("a1", "ma***@acme.example", 30) },
      { method: "POST", url: /\/authorization\/a1\/verify$/, status: 422, body: { detail: "The code is incorrect.", code: "OTP_INVALID" } },
    ]);
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Admin-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send me a code" }));
    const resend = (await within(dialog).findByRole("button", { name: /Resend in 30s/ })) as HTMLButtonElement;
    expect(resend.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/^6-digit code/), { target: { value: "000000" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verify" }));
    expect(await within(dialog).findByText("The code is incorrect.")).toBeTruthy();
  });

  it("reports a taken username from the server on the form step", async () => {
    const { dialog } = await openWizard("GUARD", [
      { method: "POST", url: /\/guards\/authorization$/, body: CODE("a1") },
      { method: "POST", url: /\/authorization\/a1\/verify$/, body: { authorization_id: "a1", status: "VERIFIED", form_expires_at: "x" } },
      { method: "POST", url: /\/guards$/, status: 409, body: { detail: "This username is already taken.", code: "USERNAME_TAKEN" } },
    ]);
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Admin-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send me a code" }));
    fireEvent.change(await within(dialog).findByLabelText(/^6-digit code/), { target: { value: "123456" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verify" }));
    fireEvent.change(await within(dialog).findByLabelText(/^Full name/), { target: { value: "Bilal Guard" } });
    fireEvent.change(within(dialog).getByLabelText(/^Username/), { target: { value: "maryam.s" } });
    fireEvent.change(within(dialog).getByLabelText(/^Email/), { target: { value: "b@acme.example" } });
    fireEvent.change(within(dialog).getByLabelText(/^Phone number/), { target: { value: "+923001234567" } });
    fireEvent.change(within(dialog).getByLabelText(/^Initial password/), { target: { value: "Strong-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send code to the Guard" }));
    expect(await within(dialog).findByText("This username is already taken.")).toBeTruthy();
    expect(within(dialog).getByLabelText(/^Username/)).toBeTruthy(); // still on the form
  });
});

describe("Guard page", () => {
  const route = (m: StaffMember) => ({ method: "GET", url: new RegExp(`/staff/${m.id}$`), body: m });

  it("lets a Supervisor edit details with their own password (PUT /staff/guards/{id})", async () => {
    const calls = mockApi([route(member()), { method: "PUT", url: /\/staff\/guards\/u1$/, body: member({ full_name: "Bilal Khan" }) }]);
    renderAs("SUPERVISOR", <StaffMemberPage />, "/staff/u1", "/staff/:id");
    expect(await screen.findByRole("heading", { name: "Bilal Guard", level: 1 })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Deactivate|Reactivate/ })).toBeNull();
    expect(screen.getByText(/recorded and reported to the Organization Admins/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Bilal Guard" });
    fireEvent.change(within(dialog).getByLabelText(/^Full name/), { target: { value: "Bilal Khan" } });
    const save = within(dialog).getByRole("button", { name: "Save changes" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // own password missing
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Sup-pass-2026" } });
    fireEvent.click(save);
    await waitFor(() => expect(sent(calls, "PUT", /\/staff\/guards\/u1$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/staff\/guards\/u1$/)[0].body).toEqual({
      full_name: "Bilal Khan", username: "bilal.g", phone_number: "+923001234567", current_password: "Sup-pass-2026",
    });
    expect(await screen.findByText("Changes saved.")).toBeTruthy();
  });

  it("sets a Guard password only when both entries match and are strong", async () => {
    const calls = mockApi([route(member()), { method: "PUT", url: /\/staff\/guards\/u1\/password$/, status: 204, body: null }]);
    renderAs("SUPERVISOR", <StaffMemberPage />, "/staff/u1", "/staff/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Set password" }));
    const dialog = screen.getByRole("dialog", { name: "Set a new password for Bilal Guard" });
    const submit = within(dialog).getByRole("button", { name: "Set password" }) as HTMLButtonElement;
    fireEvent.change(within(dialog).getByLabelText(/^New password/), { target: { value: "short" } });
    expect(within(dialog).getByText(/12 to 128/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^New password/), { target: { value: "Fresh-guard-2027" } });
    fireEvent.change(within(dialog).getByLabelText(/^Repeat the new password/), { target: { value: "Fresh-guard-2028" } });
    expect(within(dialog).getByText("The two passwords do not match.")).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^Repeat the new password/), { target: { value: "Fresh-guard-2027" } });
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Sup-pass-2026" } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => expect(sent(calls, "PUT", /\/password$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/password$/)[0].body).toEqual({ new_password: "Fresh-guard-2027", current_password: "Sup-pass-2026" });
  });

  it("assigns a Standby Guard to a post (PUT /locations/guard-assignments/{guard_id})", async () => {
    const calls = mockApi([
      route(member()),
      { method: "GET", url: /\/locations$/, body: TREE },
      { method: "PUT", url: /\/locations\/guard-assignments\/ABCDEF123456789012345678901$/, body: {} },
    ]);
    renderAs("SUPERVISOR", <StaffMemberPage />, "/staff/u1", "/staff/:id");
    expect(await screen.findByText("None: campus-wide")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Make Standby" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Assign location" }));
    const dialog = screen.getByRole("dialog", { name: "Assign the post of Bilal Guard" });
    await waitFor(() => expect((within(dialog).getByLabelText("Building") as HTMLSelectElement).options.length).toBe(2));
    fireEvent.change(within(dialog).getByLabelText("Building"), { target: { value: "b1" } });
    fireEvent.change(within(dialog).getByLabelText("Floor"), { target: { value: "f1" } });
    fireEvent.change(within(dialog).getByLabelText("Area"), { target: { value: "a1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Assign location" }));
    await waitFor(() => expect(sent(calls, "PUT", /guard-assignments/)).toHaveLength(1));
    expect(sent(calls, "PUT", /guard-assignments/)[0].body).toEqual({ building_id: "b1", floor_id: "f1", area_id: "a1" });
  });

  it("makes a Stationed Guard Standby after confirmation (DELETE /locations/guard-assignments/{guard_id})", async () => {
    const calls = mockApi([route(STATIONED), { method: "DELETE", url: /\/locations\/guard-assignments\/GHIJKL123456789012345678901$/, status: 204, body: null }]);
    renderAs("ADMIN", <StaffMemberPage />, "/staff/u2", "/staff/:id");
    expect(await screen.findByText("Admin Block › Ground › Gate")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Make Standby" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Make Hamza Guard a Standby Guard?" })).getByRole("button", { name: "Make Standby" }));
    await waitFor(() => expect(sent(calls, "DELETE", /guard-assignments/)).toHaveLength(1));
    expect(await screen.findByText("Hamza Guard is now a Standby Guard.")).toBeTruthy();
  });

  it("lets an Admin deactivate with their password; a Left Guard offers only Reactivate", async () => {
    const calls = mockApi([route(member()), { method: "POST", url: /\/staff\/u1\/deactivate$/, body: member({ status: "LEFT" }) }]);
    const first = renderAs("ADMIN", <StaffMemberPage />, "/staff/u1", "/staff/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    const dialog = screen.getByRole("dialog", { name: "Deactivate Bilal Guard?" });
    expect(within(dialog).getByText(/location assignment is removed/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Admin-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate Guard" }));
    await waitFor(() => expect(sent(calls, "POST", /deactivate$/)).toHaveLength(1));
    expect(sent(calls, "POST", /deactivate$/)[0].body).toEqual({ current_password: "Admin-pass-2026" });
    expect(await screen.findByText("Bilal Guard deactivated.")).toBeTruthy();
    first.unmount();

    mockApi([route(member({ status: "LEFT" }))]);
    renderAs("ADMIN", <StaffMemberPage />, "/staff/u1", "/staff/:id");
    expect(await screen.findByText(/This account is deactivated/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reactivate" })).toBeTruthy();
    for (const hidden of ["Edit details", "Set password", "Assign location", "Deactivate"]) {
      expect(screen.queryByRole("button", { name: hidden })).toBeNull();
    }
  });

  it("shows the server's refusal when a Supervisor opens a Supervisor", async () => {
    mockApi([{ method: "GET", url: /\/staff\/s1$/, status: 403, body: { detail: "Only an Organization Admin can view Supervisors.", code: "ADMIN_ONLY" } }]);
    renderAs("SUPERVISOR", <StaffMemberPage />, "/staff/s1", "/staff/:id");
    expect(await screen.findByText("Only an Organization Admin can view Supervisors.")).toBeTruthy();
  });
});

describe("Admin notifications about Supervisors' Guard changes", () => {
  it("lists them with a link to the Guard", async () => {
    mockApi([{ method: "GET", url: /\/notifications\?/, body: [{
      id: "n1", kind: "GUARD_UPDATED", title: "Supervisor Sana Supervisor (SUP…) updated Guard Bilal Guard (ABC…)",
      alert_id: null, manual_report_id: null, subject_user_id: "u1", created_at: "2026-11-11T10:00:00Z", read_at: null }] }]);
    renderAs("ADMIN", <NotificationsPage />);
    expect(await screen.findByText(/updated Guard Bilal Guard/)).toBeTruthy();
    expect(screen.getAllByText("Guard updated by a Supervisor").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Open Guard/ })).toBeTruthy();
  });
});
