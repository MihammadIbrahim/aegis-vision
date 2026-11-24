import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RestrictedAccessPage } from "../../features/restricted-access/pages/RestrictedAccessPage";
import { mockApi, renderAs, sent } from "../helpers";
import { AREA, BASIC, GRANT } from "./fixtures";

const COMMON = [
  { method: "GET", url: /\/restricted-access\/areas\?include_unrestricted=false$/, body: [AREA] },
  { method: "GET", url: /\/restricted-access\/areas\?include_unrestricted=true$/, body: [AREA, { ...AREA, area_id: "a2", area_name: "Lobby", is_restricted: false, active_grants: 0 }] },
  { method: "GET", url: /\/personnel\/p1$/, body: BASIC },
  { method: "GET", url: /\/restricted-access\/personnel\/p1\/grants/, body: [GRANT] },
];

describe("characterization: restricted-area access", () => {
  it("lets Admins grant access with default terms", async () => {
    const calls = mockApi([...COMMON, { method: "POST", url: /\/restricted-access\/grants$/, body: [GRANT], status: 201 }]);
    renderAs("ADMIN", <RestrictedAccessPage />, "/restricted-access?personnel=p1", "/restricted-access");
    await screen.findByText("Grant access to Ayesha Khan");
    fireEvent.click(screen.getByLabelText("IT Block › Ground › Server Room"));
    fireEvent.click(screen.getByRole("button", { name: "Grant access to 1 area(s)" }));
    await waitFor(() => expect(sent(calls, "POST", /\/grants$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/grants$/)[0].body).toEqual({
      personnel_id: "p1", area_ids: ["a1"], valid_from: null, valid_until: null, schedule: null, note: null,
    });
  });

  it("lets Admins revoke with a reason", async () => {
    const calls = mockApi([...COMMON, { method: "POST", url: /\/grants\/g1\/revoke$/, body: { ...GRANT, status: "REVOKED" } }]);
    renderAs("ADMIN", <RestrictedAccessPage />, "/restricted-access?personnel=p1", "/restricted-access");
    await screen.findByText("Grant access to Ayesha Khan");
    // Phase 4: a confirmation dialog with an optional reason replaces window.prompt.
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    const dialog = screen.getByRole("dialog", { name: "Revoke access to Server Room?" });
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Role change");
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke access" }));
    await waitFor(() => expect(sent(calls, "POST", /revoke$/)).toHaveLength(1));
    expect(sent(calls, "POST", /revoke$/)[0].body).toEqual({ reason: "Role change" });
  });

  it("is read-only for Supervisors", async () => {
    const calls = mockApi(COMMON);
    renderAs("SUPERVISOR", <RestrictedAccessPage />, "/restricted-access?personnel=p1", "/restricted-access");
    await screen.findByText("Server Room");
    expect(screen.queryByText(/Grant access to/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Restricted areas" })).toBeNull();
    expect(screen.queryByLabelText("Show revoked permissions (history)")).toBeNull();
    expect(calls.some((c) => /include_unrestricted=true|include_revoked=true/.test(c.url))).toBe(false);
  });
});
