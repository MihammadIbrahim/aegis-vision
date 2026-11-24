import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { navGroupsFor } from "../../auth/permissions";
import { renderAs } from "../../test/helpers";
import { AppShell } from "./AppShell";

describe("AppShell", () => {
  it("groups navigation by role: Supervisors get no administration group", () => {
    expect(navGroupsFor("ADMIN").map((g) => g.label)).toEqual([
      "Operations",
      "Video & Evidence",
      "Site & Infrastructure",
      "Staff",
      "People",
      "Personnel administration",
      "Reports & oversight",
      "Account & organization",
    ]);
    expect(navGroupsFor("SUPERVISOR").map((g) => [g.label, g.items.map((i) => i.label)])).toEqual([
      ["Operations", ["Dashboard", "Alerts", "Incidents", "AI Investigation", "Manual Reports", "Notifications"]],
      ["Video & Evidence", ["Live Cameras", "Recordings", "Evidence Vault", "Person Search"]],
      ["Site & Infrastructure", ["Buildings & Locations", "Cameras", "AI Configuration"]],
      ["Staff", ["Staff Management"]],
      ["People", ["Personnel Directory", "Restricted-Area Access"]],
      ["Reports & oversight", ["Reports & Analytics", "Audit Logs"]],
      ["Account & organization", ["Profile"]],
    ]);
    expect(navGroupsFor("GUARD")).toEqual([]);
    renderAs("SUPERVISOR", <AppShell>page</AppShell>);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByText("Personnel administration")).toBeNull();
    expect(within(nav).queryByRole("link", { name: /Bulk Import/ })).toBeNull();
  });

  it("marks the current page and offers a skip link to the content", () => {
    renderAs("ADMIN", <AppShell>page</AppShell>, "/restricted-access");
    expect(screen.getByRole("link", { name: /Restricted-Area Access/ }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Skip to content" }).getAttribute("href")).toBe("#main-content");
    expect(document.getElementById("main-content")?.textContent).toBe("page");
  });

  it("opens and closes the mobile menu (button, Escape)", () => {
    const { container } = renderAs("ADMIN", <AppShell>page</AppShell>);
    const open = screen.getByRole("button", { name: "Open menu" });
    expect(open.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(open);
    expect(container.querySelector(".shell")?.className).toContain("is-menu-open");
    expect(open.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(container.querySelector(".shell")?.className).not.toContain("is-menu-open");
    fireEvent.click(open);
    fireEvent.click(screen.getByRole("button", { name: "Close menu" }));
    expect(container.querySelector(".shell")?.className).not.toContain("is-menu-open");
  });

  it("collapses the desktop sidebar and remembers it", () => {
    const { container, unmount } = renderAs("ADMIN", <AppShell>page</AppShell>);
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(container.querySelector(".shell")?.className).toContain("is-collapsed");
    expect(localStorage.getItem("cctv.sidebarCollapsed")).toBe("1");
    unmount();
    renderAs("ADMIN", <AppShell>page</AppShell>);
    expect(screen.getByRole("button", { name: "Expand sidebar" }).getAttribute("aria-pressed")).toBe("true");
    localStorage.clear();
  });

  it("shows the user, role, theme switch and sign-out", () => {
    renderAs("ADMIN", <AppShell>page</AppShell>);
    expect(screen.getByText("Admin A")).toBeTruthy();
    expect(screen.getByText("Organization Admin")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Dark theme|Light theme/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
  });
});
