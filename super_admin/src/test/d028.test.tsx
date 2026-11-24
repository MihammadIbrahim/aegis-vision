// Decision D-028: the Super Admin's per-organization AI Feature Access card (Organization Details). One feature per
// click, with the password (SA-SEC-4); statuses Allowed / Blocked for this organization / Disabled globally.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { OrganizationAiPermission, OrganizationDetail } from "../api/types";
import { OrganizationDetailPage } from "../features/organizations/OrganizationDetailPage";
import { mockApi, renderAs, sent } from "./helpers";

const permission = (module_key: string, label: string, status: OrganizationAiPermission["status"], core = true): OrganizationAiPermission => ({
  module_key, feature: module_key, label, core, blocked: status === "BLOCKED", platform_enabled: status !== "DISABLED_GLOBALLY", status,
});

const DETAIL: OrganizationDetail = {
  organization_id: "ORG-00000000001", name: "Riverside University", official_email: "info@riverside.example.com",
  official_phone_number: "+923001112222", status: "ACTIVE", created_at: "2026-10-27T09:00:00Z",
  main_admin: { full_name: "Hina Malik", username: "hina", email: "hina@riverside.example.com", phone_number: "+923001112222" },
  campus_count: 1, camera_count: 5, registered_address: null, campuses: [],
  ai_permissions: [
    permission("human_detection", "Human detection & single-camera tracking", "ALLOWED"),
    permission("person_reid", "Cross-camera person re-identification (Re-ID)", "DISABLED_GLOBALLY"),
    permission("weapon_detection", "Weapon detection", "BLOCKED"),
    permission("fight_detection", "Fight / violence detection", "ALLOWED"),
    permission("face_recognition", "Restricted-area face recognition", "DISABLED_GLOBALLY", false),
  ],
} as OrganizationDetail;

const route = "/organizations/:organizationId";
const path = "/organizations/ORG-00000000001";

describe("AI Feature Access (Organization Controls)", () => {
  it("shows the four core features with their status", async () => {
    mockApi([{ method: "GET", url: /\/organizations\/ORG-00000000001$/, body: DETAIL }]);
    renderAs("NORMAL", <OrganizationDetailPage />, path, route);
    const card = await screen.findByTestId("ai-access");
    expect(within(card).getByRole("heading", { name: "AI Feature Access (Organization Controls)" })).toBeTruthy();
    expect(within(card).getAllByRole("listitem")).toHaveLength(4);
    expect(within(card).queryByText("Restricted-area face recognition")).toBeNull();
    expect(within(card).getAllByText("Allowed")).toHaveLength(2);
    expect(within(card).getByText("Blocked for this organization")).toBeTruthy();
    expect(within(card).getByText("Disabled globally")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Unblock Weapon detection" })).toBeTruthy();
  });

  it("blocks one feature with the password and reloads", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/organizations\/ORG-00000000001$/, body: DETAIL },
      { method: "PUT", url: /\/ai-permissions$/, body: { organization_id: "ORG-00000000001", ai_permissions: DETAIL.ai_permissions } },
    ]);
    renderAs("NORMAL", <OrganizationDetailPage />, path, route);
    fireEvent.click(await screen.findByRole("button", { name: "Block Fight / violence detection" }));
    const dialog = screen.getByRole("dialog", { name: "Block Fight / violence detection for Riverside University" });
    expect(within(dialog).getByText(/stops at once on every camera/)).toBeTruthy();
    const submit = within(dialog).getByRole("button", { name: "Block feature" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "Secret-pass-2026" } });
    fireEvent.click(submit);
    await waitFor(() => expect(sent(calls, "PUT", /\/ai-permissions$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/ai-permissions$/)[0].url).toMatch(/\/platform\/organizations\/ORG-00000000001\/ai-permissions$/);
    expect(sent(calls, "PUT", /\/ai-permissions$/)[0].body).toEqual({ module_key: "fight_detection", blocked: true, current_password: "Secret-pass-2026" });
    expect(await screen.findByText("Fight / violence detection is now blocked for Riverside University.")).toBeTruthy();
    expect(sent(calls, "GET", /ORG-00000000001$/)).toHaveLength(2);
  });

  it("unblocks with an honest note and shows a wrong password in the dialog", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/organizations\/ORG-00000000001$/, body: DETAIL },
      { method: "PUT", url: /\/ai-permissions$/, status: 403, body: { detail: "The current password is incorrect." } },
    ]);
    renderAs("NORMAL", <OrganizationDetailPage />, path, route);
    fireEvent.click(await screen.findByRole("button", { name: "Unblock Weapon detection" }));
    const dialog = screen.getByRole("dialog", { name: "Unblock Weapon detection for Riverside University" });
    expect(within(dialog).getByText(/No camera is switched back on automatically/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/^Your current password/), { target: { value: "wrong" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Unblock feature" }));
    expect(await within(dialog).findByText("The current password is incorrect.")).toBeTruthy();
    expect(sent(calls, "PUT", /\/ai-permissions$/)[0].body).toEqual({ module_key: "weapon_detection", blocked: false, current_password: "wrong" });
  });
});
