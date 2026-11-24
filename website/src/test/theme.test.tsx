import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell as Layout } from "../app/layouts/AppShell";
import { statusLook } from "../lib/status";
import { applyTheme, currentTheme, THEME_KEY } from "../lib/theme";
import { renderAs } from "./helpers";

function systemPrefersDark(dark: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: dark && query.includes("dark"),
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

describe("theme", () => {
  afterEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  it("follows the system until the user chooses", () => {
    systemPrefersDark(true);
    expect(currentTheme()).toBe("dark");
    localStorage.setItem(THEME_KEY, "light");
    expect(currentTheme()).toBe("light");
    applyTheme();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("switches from the topbar and remembers the choice", () => {
    systemPrefersDark(false);
    applyTheme();
    renderAs("ADMIN", <Layout>page</Layout>);
    const toggle = screen.getByRole("button", { name: "Dark theme" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: "Light theme" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("status map", () => {
  it("covers the alert lifecycle and keeps red for the HIGH alert types only", () => {
    expect(["NEW", "ACKNOWLEDGED", "RESPONDING", "RESOLVED"].map((s) => statusLook(s).label)).toEqual([
      "New", "Acknowledged", "Responding", "Resolved",
    ]);
    expect(statusLook("WEAPON").tone).toBe("critical");
    expect(statusLook("FIGHT_VIOLENCE").tone).toBe("critical");
    expect(statusLook("RESTRICTED_AREA").tone).toBe("unauthorized");
    expect(statusLook("UNKNOWN_IDENTITY").tone).toBe("unknown");
    expect(statusLook("ESCALATED").tone).not.toBe("critical");
  });

  it("falls back to a readable neutral label", () => {
    const look = statusLook("SOMETHING_NEW");
    expect([look.tone, look.label]).toEqual(["normal", "something new"]);
  });
});
