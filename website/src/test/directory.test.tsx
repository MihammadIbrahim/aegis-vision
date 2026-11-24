import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DirectoryPage } from "../features/personnel/pages/DirectoryPage";
import { mockApi, renderAs } from "./helpers";

const row = {
  id: "p1",
  personnel_id: "PSN-ABCDEFGHJK",
  external_id: "STU-001",
  full_name: "Ayesha Khan",
  category: { id: "c1", code: "STUDENT", label: "Student" },
  department: "CS",
  designation: null,
  status: "ACTIVE",
  start_date: null,
  end_date: null,
  assigned_location: null,
};

describe("personnel directory", () => {
  it("is read-only for Supervisors: no create or import actions, no contact column", async () => {
    mockApi([
      [/\/personnel\/categories/, [{ id: "c1", code: "STUDENT", label: "Student" }]],
      [/\/personnel\?/, { items: [{ ...row, email: null, phone: null }], total: 1, limit: 25, offset: 0 }],
    ]);
    renderAs("SUPERVISOR", <DirectoryPage />);
    await waitFor(() => expect(screen.getByText("Ayesha Khan")).toBeTruthy());
    expect(screen.queryByRole("link", { name: "Add person" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Bulk import" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Email" })).toBeNull();
    expect(screen.getByText(/Read-only/)).toBeTruthy();
  });

  it("offers Admins registration and import", async () => {
    mockApi([
      [/\/personnel\/categories/, []],
      [/\/personnel\?/, { items: [{ ...row, email: "a@example.com", phone: null }], total: 1, limit: 25, offset: 0 }],
    ]);
    renderAs("ADMIN", <DirectoryPage />);
    await waitFor(() => expect(screen.getByText("a@example.com")).toBeTruthy());
    expect(screen.getByRole("link", { name: "Add person" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Bulk import" })).toBeTruthy();
  });
});
