import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { PersonnelDetailPage } from "../../features/personnel/pages/PersonnelDetailPage";
import { mockApi, renderAs, sent } from "../helpers";
import { BASIC, DETAIL, GRANT } from "./fixtures";

describe("characterization: person details", () => {
  it("gives Admins the full record, edit and deactivation with a reason", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/personnel\/p1$/, body: DETAIL },
      { method: "GET", url: /\/restricted-access\/personnel\/p1\/grants/, body: [GRANT] },
      { method: "GET", url: /\/personnel\/p1\/history$/, body: [] },
      { method: "POST", url: /\/personnel\/p1\/deactivate$/, body: { ...DETAIL, status: "INACTIVE" } },
    ]);
    renderAs("ADMIN", <PersonnelDetailPage />, "/personnel/p1", "/personnel/:id");
    await screen.findByText("Private Street 1");
    expect(screen.getByRole("link", { name: "Edit" }).getAttribute("href")).toBe("/personnel/p1/edit");
    expect(screen.getByText("Server Room")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "History" })).toBeTruthy();
    // Phase 4: an accessible confirmation dialog (with an optional reason) replaces window.prompt.
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    const dialog = screen.getByRole("dialog", { name: "Deactivate Ayesha Khan?" });
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Graduated");
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(sent(calls, "POST", /deactivate$/)).toHaveLength(1));
    expect(sent(calls, "POST", /deactivate$/)[0].body).toEqual({ reason: "Graduated" });
  });

  it("shows Supervisors basic details only, without edit, history, contact data or photo", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/personnel\/p1$/, body: BASIC },
      { method: "GET", url: /\/restricted-access\/personnel\/p1\/grants/, body: [GRANT] },
    ]);
    renderAs("SUPERVISOR", <PersonnelDetailPage />, "/personnel/p1", "/personnel/:id");
    await screen.findByText("PSN-ABCDEFGHJK");
    expect(screen.queryByRole("link", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deactivate" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "History" })).toBeNull();
    expect(screen.getByText(/Photos are shown to Supervisors only within an authorized identity review/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "View access" })).toBeTruthy();
    expect(calls.some((c) => /history|photo/.test(c.url))).toBe(false); // never requested
  });
});
