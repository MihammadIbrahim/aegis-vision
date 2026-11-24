import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { canManagePersonnel, canViewPersonnel, navFor } from "../auth/permissions";
import { AppShell as Layout } from "../app/layouts/AppShell";
import { AdminOnly } from "../auth/AdminOnly";
import { renderAs } from "./helpers";

describe("role permissions in the UI", () => {
  it("lets only Organization Admins manage personnel", () => {
    expect(canManagePersonnel("ADMIN")).toBe(true);
    expect(canManagePersonnel("SUPERVISOR")).toBe(false);
    expect(canManagePersonnel("GUARD")).toBe(false);
    expect(canViewPersonnel("SUPERVISOR")).toBe(true);
    expect(canViewPersonnel("GUARD")).toBe(false);
  });

  it("shows Supervisors no import or configuration pages, and Guards nothing", () => {
    expect(navFor("ADMIN").map((i) => i.label)).toEqual([
      "Dashboard",
      "Alerts",
      "Incidents",
      "AI Investigation",
      "Manual Reports",
      "Notifications",
      "Live Cameras",
      "Recordings",
      "Evidence Vault",
      "Person Search",
      "Buildings & Locations",
      "Cameras",
      "AI Configuration",
      "Staff Management",
      "User Records",
      "Personnel Directory",
      "Restricted-Area Access",
      "Bulk Import",
      "Import History",
      "Categories & Fields",
      "Reports & Analytics",
      "Audit Logs",
      "Organization Settings",
      "Account & Ownership",
      "Profile",
    ]);
    // Only the Main Admin approves campus requests (D-024).
    expect(navFor("ADMIN", true).map((i) => i.label)).toContain("Campus Requests");
    expect(navFor("ADMIN").map((i) => i.label)).not.toContain("Campus Requests");
    expect(navFor("SUPERVISOR").map((i) => i.label)).toEqual([
      "Dashboard",
      "Alerts",
      "Incidents",
      "AI Investigation",
      "Manual Reports",
      "Notifications",
      "Live Cameras",
      "Recordings",
      "Evidence Vault",
      "Person Search",
      "Buildings & Locations",
      "Cameras",
      "AI Configuration",
      "Staff Management",
      "Personnel Directory",
      "Restricted-Area Access",
      "Reports & Analytics",
      "Audit Logs",
      "Profile",
    ]);
    expect(navFor("SUPERVISOR", true).map((i) => i.label)).not.toContain("Campus Requests");
    expect(navFor("GUARD")).toEqual([]);
  });

  it("renders the Supervisor's navigation without Admin entries", () => {
    renderAs("SUPERVISOR", <Layout>content</Layout>);
    expect(screen.getByRole("link", { name: "Personnel Directory" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Bulk Import" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Categories & Fields" })).toBeNull();
  });

  it("blocks Admin-only pages for Supervisors", () => {
    renderAs("SUPERVISOR", <AdminOnly>secret form</AdminOnly>);
    expect(screen.getByText("Only an Organization Admin can use this page.").getAttribute("role")).toBe("alert");
    expect(screen.queryByText("secret form")).toBeNull();
  });
});
