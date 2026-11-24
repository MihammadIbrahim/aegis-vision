// Public landing page, sign-in and Create Account entry points (POST /auth/organization-registrations…).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "../app/App";
import { StaticAuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";
import { CAPABILITY_GROUPS } from "../features/landing/content";
import { campusNameProblem, cleanOrganizationName, organizationNameProblem } from "../features/registration/RegisterPage";
import { THEME_KEY } from "../lib/theme";
import { mockApi, sent, sessionFor } from "./helpers";

function at(path: string, signedIn = false) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <StaticAuthProvider session={signedIn ? sessionFor("ADMIN") : null}>
        <ToastProvider>
          <App />
        </ToastProvider>
      </StaticAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("public landing page", () => {
  it("is what a signed-out visitor sees at /", () => {
    mockApi([]);
    at("/");
    expect(screen.getByRole("heading", { level: 1, name: /Cameras, alerts and security staff/ })).toBeTruthy();
    const sections = screen.getByRole("navigation", { name: "Sections" });
    for (const name of ["Capabilities", "How it works", "Security & roles", "Architecture"]) {
      expect(within(sections).getByRole("link", { name }).getAttribute("href")).toMatch(/^#/);
    }
    expect(screen.getByText(/AI outputs are decision-support signals, not proof/)).toBeTruthy();
    expect(screen.getByRole("img", { name: "Campus map" })).toBeTruthy();
    expect(screen.getByText(/Illustration with sample data/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull(); // not the sign-in form
  });

  it("labels every capability honestly: planned work is never shown as available", () => {
    mockApi([]);
    at("/");
    const card = (title: string) => screen.getByRole("heading", { level: 4, name: title }).closest("li") as HTMLElement;
    expect(within(card("Weapon detection")).getByText("Planned")).toBeTruthy();
    expect(within(card("Fight / violence detection")).getByText("Planned")).toBeTruthy();
    expect(within(card("RAG security assistant")).getByText("Planned")).toBeTruthy();
    expect(within(card("Human detection & single-camera tracking")).getByText("Partly built")).toBeTruthy();
    expect(within(card("Location-aware alert routing")).getByText("Available")).toBeTruthy();
    const planned = CAPABILITY_GROUPS.flatMap((g) => g.items).filter((c) => c.status === "PLANNED").map((c) => c.title);
    expect(planned).toEqual(expect.arrayContaining(["Cross-camera person re-identification", "Guided incident investigation", "Live CCTV monitoring"]));
  });

  it("Sign in opens the sign-in page", async () => {
    mockApi([]);
    at("/");
    fireEvent.click(screen.getAllByRole("link", { name: "Sign in" })[0]);
    expect(await screen.findByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Create an account" }).getAttribute("href")).toBe("/register");
    expect(screen.getByRole("link", { name: "AI CCTV Security, home" }).getAttribute("href")).toBe("/");
  });

  it("Create account opens the registration form", async () => {
    mockApi([]);
    at("/");
    fireEvent.click(screen.getAllByRole("link", { name: /Create account/ })[0]);
    expect(await screen.findByRole("heading", { name: "Create an organization account" })).toBeTruthy();
    expect(screen.getByRole("form", { name: "Organization registration" })).toBeTruthy();
  });

  it("switches the theme and keeps it for the sign-in page", async () => {
    mockApi([]);
    localStorage.setItem(THEME_KEY, "light");
    at("/");
    const toggle = screen.getByRole("button", { name: "Switch to dark theme" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light theme" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getAllByRole("link", { name: "Sign in" })[0]);
    await screen.findByRole("button", { name: "Continue" });
    expect(screen.getByRole("button", { name: /Light theme/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("opens and closes the section menu on small screens", () => {
    mockApi([]);
    at("/");
    const menu = screen.getByRole("button", { name: "Open menu" });
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(menu);
    expect(screen.getByRole("button", { name: "Close menu" }).getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Open menu" })).toBeTruthy();
  });

  it("sends signed-in users from / to the dashboard", async () => {
    mockApi([]);
    at("/", true);
    expect(await screen.findByRole("link", { name: "Dashboard", current: "page" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Cameras, alerts and security staff/ })).toBeNull();
  });

  it("keeps deep links on the sign-in page for signed-out visitors", () => {
    mockApi([]);
    at("/alerts");
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
  });
});

describe("Create account (organization registration)", () => {
  const CODE = { registration_id: "r1", status: "PENDING_EMAIL_VERIFICATION", email: "ay***@acme.example.com", delivery: "EMAIL",
    otp_expires_in_seconds: 300, resend_available_in_seconds: 0, registration_expires_at: "2026-11-12T12:00:00Z" };
  const DONE = { status: "COMPLETED", organization_id: "ABCDEF12345678901", organization_name: "Acme Security Group",
    official_email: "ayesha@acme.example.com", official_phone_number: "+923001234567", campus_name: "Main Campus",
    main_admin: { user_id: "KQMTZX48271930561", full_name: "Ayesha Khan", username: "ayesha.k", email: "ayesha@acme.example.com",
      phone_number: "+923001234567", role: "ADMIN", is_main_admin: true }, registered_at: "2026-11-12T11:00:00Z" };

  function fill() {
    const set = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
    set(/^Organization name/, "  Acme   Security Group ");
    set(/^Campus name/, " City  Campus ");
    set(/^Full name/, "Ayesha Khan");
    set(/^Username/, "ayesha.k");
    set(/^Official email/, "ayesha@acme.example.com");
    set(/^Phone number/, "+92 300 1234567");
    set(/^Password/, "Strong-pass-2026");
    set(/^Confirm password/, "Strong-pass-2026");
  }

  it("validates, sends the form, verifies the code and shows the Organization ID", async () => {
    const calls = mockApi([
      { method: "POST", url: /\/auth\/organization-registrations$/, status: 201, body: CODE },
      { method: "POST", url: /\/organization-registrations\/r1\/resend-otp$/, body: CODE },
      { method: "POST", url: /\/organization-registrations\/r1\/verify$/, body: DONE },
    ]);
    at("/register");
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(screen.getByText("Enter the organization name.")).toBeTruthy();
    expect(sent(calls, "POST", /registrations$/)).toHaveLength(0);
    fill();
    fireEvent.change(screen.getByLabelText(/^Confirm password/), { target: { value: "Strong-pass-2027" } });
    expect(screen.getByText("The passwords do not match.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/^Confirm password/), { target: { value: "Strong-pass-2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(await screen.findByText(/ay\*\*\*@acme\.example\.com/)).toBeTruthy();
    expect(sent(calls, "POST", /registrations$/)[0].body).toEqual({
      organization_name: "Acme Security Group", campus_name: "City Campus", admin_full_name: "Ayesha Khan", username: "ayesha.k",
      email: "ayesha@acme.example.com", phone_number: "+92 300 1234567", password: "Strong-pass-2026",
    });
    fireEvent.click(screen.getByRole("button", { name: "Send a new code" }));
    await waitFor(() => expect(sent(calls, "POST", /resend-otp$/)).toHaveLength(1));
    fireEvent.change(screen.getByLabelText(/^Verification code/), { target: { value: "12x3456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and create the organization" }));
    expect(await screen.findByText("ABCDEF12345678901")).toBeTruthy();
    expect(sent(calls, "POST", /verify$/)[0].body).toEqual({ otp: "123456" });
    expect(screen.getByText("Acme Security Group", { selector: "strong" })).toBeTruthy();
    // Both IDs are shown with their own copy buttons (decision D-021).
    expect(screen.getByText("KQMTZX48271930561")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy Organization ID" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy Main Admin ID" })).toBeTruthy();
    expect(screen.getByText(/need the exact organization name and this Organization ID/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to sign-in" }).getAttribute("href")).toBe("/login");
  });

  it("shows the server's refusal (taken name, wrong code)", async () => {
    mockApi([{ method: "POST", url: /registrations$/, status: 409, body: { detail: "An organization with this name already exists.", code: "ORGANIZATION_NAME_TAKEN" } }]);
    at("/register");
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(await screen.findByText("An organization with this name already exists.")).toBeTruthy();
    mockApi([
      { method: "POST", url: /registrations$/, status: 201, body: { ...CODE, resend_available_in_seconds: 60 } },
      { method: "POST", url: /verify$/, status: 400, body: { detail: "The code is incorrect.", code: "OTP_INVALID" } },
    ]);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect((await screen.findByRole("button", { name: /Send a new code in/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^Verification code/), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and create the organization" }));
    expect(await screen.findByText("The code is incorrect.")).toBeTruthy();
  });

  it("cleans and checks the organization name like the server", () => {
    expect(cleanOrganizationName("  Acme   Security  ")).toBe("Acme Security");
    expect(organizationNameProblem(" ")).toBe("Enter the organization name.");
    expect(organizationNameProblem("A")).toMatch(/2 to 150/);
    expect(organizationNameProblem("--")).toMatch(/letters or digits/);
    expect(organizationNameProblem("Acme")).toBeUndefined();
    expect(campusNameProblem(" ")).toBe("Enter the campus name.");
    expect(campusNameProblem("x".repeat(101))).toMatch(/2 to 100/);
    expect(campusNameProblem("City Campus")).toBeUndefined();
  });
});
