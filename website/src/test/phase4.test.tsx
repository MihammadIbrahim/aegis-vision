// Behaviour changed in Phase 4: browser prompts became accessible dialogs, and the delayed redirect
// after "saved with warnings" became a toast. Request URLs and bodies stay as before.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ImportHistoryPage } from "../features/personnel-import/pages/ImportHistoryPage";
import { PersonnelDetailPage } from "../features/personnel/pages/PersonnelDetailPage";
import { PersonnelFormPage } from "../features/personnel/pages/PersonnelFormPage";
import { RestrictedAccessPage } from "../features/restricted-access/pages/RestrictedAccessPage";
import { AREA, BASIC, batch, CATEGORIES, DETAIL, FIELDS, GRANT } from "./characterization/fixtures";
import { mockApi, renderAs, sent } from "./helpers";

const DETAIL_LOAD = [
  { method: "GET", url: /\/personnel\/p1$/, body: DETAIL },
  { method: "GET", url: /\/restricted-access\/personnel\/p1\/grants/, body: [] },
  { method: "GET", url: /\/personnel\/p1\/history$/, body: [] },
];

describe("person details dialogs", () => {
  it("sends nothing when deactivation is cancelled", async () => {
    const calls = mockApi(DETAIL_LOAD);
    renderAs("ADMIN", <PersonnelDetailPage />, "/personnel/p1", "/personnel/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "POST", /deactivate$/)).toHaveLength(0);
  });

  it("sends a null reason when none is given and confirms with a toast", async () => {
    const calls = mockApi([...DETAIL_LOAD, { method: "POST", url: /deactivate$/, body: { ...DETAIL, status: "INACTIVE" } }]);
    renderAs("ADMIN", <PersonnelDetailPage />, "/personnel/p1", "/personnel/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(sent(calls, "POST", /deactivate$/)).toHaveLength(1));
    expect(sent(calls, "POST", /deactivate$/)[0].body).toEqual({ reason: null });
    expect(await screen.findByText("Person deactivated.")).toBeTruthy();
  });

  it("asks before reactivating", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/personnel\/p1$/, body: { ...DETAIL, status: "INACTIVE", deactivated_at: "2026-11-01T00:00:00Z" } },
      ...DETAIL_LOAD.slice(1),
      { method: "POST", url: /reactivate$/, body: DETAIL },
    ]);
    renderAs("ADMIN", <PersonnelDetailPage />, "/personnel/p1", "/personnel/:id");
    fireEvent.click(await screen.findByRole("button", { name: "Reactivate" }));
    expect(screen.getByRole("dialog", { name: "Reactivate Ayesha Khan?" })).toBeTruthy();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(sent(calls, "POST", /reactivate$/)).toHaveLength(1));
  });
});

describe("personnel form", () => {
  it("shows warnings as a toast and opens the record straight away", async () => {
    mockApi([
      { method: "GET", url: /\/personnel\/fields$/, body: FIELDS },
      { method: "GET", url: /\/personnel\/categories$/, body: CATEGORIES },
      { method: "GET", url: /\/personnel\/custom-fields$/, body: [] },
      { method: "GET", url: /\/locations$/, body: [] },
      { method: "POST", url: /\/api\/v1\/personnel$/, status: 201, body: {
        personnel: { id: "new-id" }, warnings: [{ field: null, code: "NO_CONTACT", message: "No email address or phone number is given." }] } },
    ]);
    renderAs("ADMIN", <PersonnelFormPage />, "/personnel/new", "/personnel/new");
    await screen.findByText("Visitor");
    fireEvent.change(screen.getByLabelText(/Full name/), { target: { value: "Guest" } });
    fireEvent.change(screen.getByLabelText(/Personnel category/), { target: { value: "cat-visitor" } });
    fireEvent.change(screen.getByLabelText(/End Date/), { target: { value: "2030-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Register person" }));
    await screen.findByText("navigated away");
    expect(screen.getByText(/Saved with warnings: No email address or phone number is given\./)).toBeTruthy();
  });
});

describe("restricted access dialogs", () => {
  const LOAD = [
    { method: "GET", url: /areas\?include_unrestricted=false$/, body: [AREA] },
    { method: "GET", url: /areas\?include_unrestricted=true$/, body: [AREA] },
    { method: "GET", url: /\/personnel\/p1$/, body: BASIC },
    { method: "GET", url: /\/restricted-access\/personnel\/p1\/grants/, body: [{ ...GRANT, valid_until: "2026-12-01T00:00:00Z" }] },
  ];

  it("changes validity in a dialog, sending the rest of the grant unchanged", async () => {
    const calls = mockApi([...LOAD, { method: "PUT", url: /\/grants\/g1$/, body: GRANT }]);
    renderAs("ADMIN", <RestrictedAccessPage />, "/restricted-access?personnel=p1", "/restricted-access");
    fireEvent.click(await screen.findByRole("button", { name: "Change validity" }));
    const dialog = screen.getByRole("dialog", { name: "Change validity: Server Room" });
    fireEvent.change(within(dialog).getByLabelText(/Valid until/), { target: { value: "" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save validity" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/grants\/g1$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/grants\/g1$/)[0].body).toEqual({
      valid_from: GRANT.valid_from, valid_until: null, schedule: null, note: null,
    });
    expect(await screen.findByText("Validity updated.")).toBeTruthy();
  });

  it("asks before clearing a restricted area", async () => {
    const calls = mockApi([...LOAD, { method: "DELETE", url: /\/areas\/a1$/, body: null, status: 204 }]);
    renderAs("ADMIN", <RestrictedAccessPage />, "/restricted-access", "/restricted-access");
    fireEvent.click(await screen.findByRole("button", { name: "Clear" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear restriction" }));
    await waitFor(() => expect(sent(calls, "DELETE", /\/areas\/a1$/)).toHaveLength(1));
  });
});

describe("import history dialogs", () => {
  it("rolls back with a reason and discards only after confirmation", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/personnel-imports\?limit=50&offset=0$/, body: [
        batch("IMPORTED", { id: "b1", imported_count: 3 }), batch("VALIDATED", { id: "b2", original_filename: "draft.xlsx" })] },
      { method: "POST", url: /\/b1\/rollback$/, body: {} },
      { method: "DELETE", url: /\/personnel-imports\/b2$/, body: null, status: 204 },
    ]);
    renderAs("ADMIN", <ImportHistoryPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Roll back" }));
    const dialog = screen.getByRole("dialog", { name: "Roll back staff.xlsx?" });
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Wrong file");
    fireEvent.click(within(dialog).getByRole("button", { name: "Roll back import" }));
    await waitFor(() => expect(sent(calls, "POST", /rollback$/)).toHaveLength(1));
    expect(sent(calls, "POST", /rollback$/)[0].body).toEqual({ reason: "Wrong file" });

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent(calls, "DELETE", /b2$/)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Discard draft.xlsx?" })).getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(sent(calls, "DELETE", /b2$/)).toHaveLength(1));
  });
});
