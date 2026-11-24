// Since the access-note fix, GET /restricted-access/areas leaves `access_note` out for Supervisors.
// Their pages must work without the field and never show a note.
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DashboardPage } from "../features/dashboard/pages/DashboardPage";
import { RestrictedAccessPage } from "../features/restricted-access/pages/RestrictedAccessPage";
import { AREA, BASIC, GRANT } from "./characterization/fixtures";
import { mockApi, renderAs } from "./helpers";

const { access_note, ...SUPERVISOR_AREA } = AREA;

describe("restricted areas without access_note (Supervisor)", () => {
  it("the Restricted-Area Access page still lists areas and current authorizations", async () => {
    mockApi([
      { method: "GET", url: /\/restricted-access\/areas\?include_unrestricted=false$/, body: [SUPERVISOR_AREA] },
      { method: "GET", url: /\/personnel\/p1$/, body: BASIC },
      { method: "GET", url: /\/restricted-access\/personnel\/p1\/grants/, body: [GRANT] },
    ]);
    renderAs("SUPERVISOR", <RestrictedAccessPage />, "/restricted-access?personnel=p1", "/restricted-access");
    expect((await screen.findAllByText("Server Room")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/note/i)).toBeNull();
  });

  it("the dashboard still lists restricted areas with their current grant counts", async () => {
    mockApi([
      { method: "GET", url: /\/supervisor\/dashboard$/, body: {
        organization: { organization_id: "ABCDEF12345678901", name: "Acme", status: "ACTIVE" },
        campus: { name: "Main Campus", status: "ACTIVE" },
        supervisor: { user_id: "S1", full_name: "Supervisor S" },
        summary: { active_guards: 0, cameras: 0 } } },
      { method: "GET", url: /\/restricted-access\/areas\?include_unrestricted=false$/, body: [SUPERVISOR_AREA] },
      [/\/alerts\?/, []], [/\/manual-reports\?/, []], [/\/notifications\?/, []], [/\/locations$/, []],
    ]);
    renderAs("SUPERVISOR", <DashboardPage />);
    await waitFor(() => expect(screen.getByText(/1 current access grant$/)).toBeTruthy());
    expect(screen.getByText("Server Room")).toBeTruthy();
  });
});
