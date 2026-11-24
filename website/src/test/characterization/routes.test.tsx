import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "../../app/App";
import { StaticAuthProvider } from "../../auth/AuthContext";
import { mockApi, sessionFor } from "../helpers";

function at(role: "ADMIN" | "SUPERVISOR", path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <StaticAuthProvider session={sessionFor(role)}>
        <App />
      </StaticAuthProvider>
    </MemoryRouter>,
  );
}

describe("characterization: routes", () => {
  it.each(["/personnel/new", "/personnel/p1/edit", "/personnel/imports", "/personnel/imports/new", "/personnel/settings"])(
    "blocks %s for Supervisors without calling the API",
    (path) => {
      const calls = mockApi([]);
      at("SUPERVISOR", path);
      expect(screen.getByRole("alert").textContent).toContain("Only an Organization Admin can use this page.");
      // The shell's own requests (the unread-notifications count and, since D-026, ringing calls) are the only ones;
      // the blocked page makes none.
      expect(calls.filter((c) => !c.url.startsWith("/api/v1/notifications") && c.url !== "/api/v1/calls")).toHaveLength(0);
    },
  );

  it("shows the sign-in page when nobody is signed in", () => {
    render(
      <MemoryRouter initialEntries={["/personnel"]}>
        <StaticAuthProvider session={null}>
          <App />
        </StaticAuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
  });

  it("sends unknown paths to the dashboard (Phase 5; earlier: the directory)", async () => {
    mockApi([]);
    at("ADMIN", "/nowhere");
    expect(await screen.findByRole("link", { name: "Dashboard", current: "page" })).toBeTruthy();
  });
});
