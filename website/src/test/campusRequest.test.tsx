// Centred sign-in / registration layout, and "Already Registered Organization?" campus requests
// (POST /auth/campus-requests…, then the Main Admin approves; sign-in says "approval pending" before that).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "../app/App";
import { StaticAuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";
import { campusRequestProblems, cleanCampusName } from "../features/registration/CampusRequestPage";
import { mockApi, sent } from "./helpers";

function at(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <StaticAuthProvider session={null}>
        <ToastProvider>
          <App />
        </ToastProvider>
      </StaticAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => localStorage.clear());

const CODE = {
  application_id: "a1", status: "PENDING_EMAIL_VERIFICATION", email: "ha***@north.example.com", delivery: "EMAIL",
  otp_expires_in_seconds: 300, resend_available_in_seconds: 0, application_expires_at: "2026-11-12T13:00:00Z",
};
const PENDING = {
  status: "PENDING_APPROVAL", organization_name: "Lakeside Institute", campus_name: "North Campus",
  email: "hamza@north.example.com", email_verified_at: "2026-11-12T12:00:00Z", decision_deadline: "2026-11-15T12:00:00Z",
};

function fill(overrides: Record<string, string> = {}) {
  const values: Record<string, string> = {
    "Organization name": "Lakeside Institute",
    "Organization ID": " zhpxmu29711010321 ",
    "Proposed campus name": "  North   Campus ",
    "Full name": "Hamza Raza",
    Username: "hamza.r",
    "Official email": "hamza@north.example.com",
    "Phone number": "+92 321 7654321",
    Password: "Northside-pass-2026",
    "Confirm password": "Northside-pass-2026",
    ...overrides,
  };
  for (const [label, value] of Object.entries(values)) {
    fireEvent.change(screen.getByLabelText(new RegExp(`^${label}`)), { target: { value } });
  }
}

describe("centred sign-in layout", () => {
  it("shows one centred card with the top bar, and no side panel", () => {
    mockApi([]);
    at("/login");
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.queryByText(/Multi-campus isolation/)).toBeNull();
    expect(screen.getByRole("link", { name: "AI CCTV Security, home" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: /Back to home/ }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("button", { name: /Dark theme|Light theme/ })).toBeTruthy();
    expect(screen.getByRole("main").className).toMatch(/max-w-md/);
    expect(screen.getByRole("heading", { level: 1, name: "Sign in" })).toBeTruthy();
  });

  it("offers both ways to register below the sign-in form", () => {
    mockApi([]);
    at("/login");
    expect(screen.getByRole("link", { name: "Create an account" }).getAttribute("href")).toBe("/register");
    expect(screen.getByRole("link", { name: "Add a campus / branch" }).getAttribute("href")).toBe("/register/campus");
  });

  it("explains a pending campus request at sign-in", async () => {
    mockApi([
      {
        method: "POST",
        url: /\/auth\/organization-admin\/login$/,
        status: 403,
        body: {
          detail: "Your campus request is verified and waiting for the Main Admin's approval. You can sign in once it is approved.",
          code: "ADMIN_APPROVAL_PENDING",
        },
      },
    ]);
    at("/login");
    for (const [label, value] of [
      ["Organization name", "Lakeside Institute"],
      ["Organization ID", "ZHPXMU29711010321"],
      ["Email", "hamza@north.example.com"],
      ["Password", "Northside-pass-2026"],
    ]) {
      fireEvent.change(screen.getByLabelText(new RegExp(`^${label}`)), { target: { value } });
    }
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(/waiting for the Main Admin's approval/)).toBeTruthy();
    expect(screen.queryByLabelText(/^Verification code/)).toBeNull();
  });
});

describe("registration switch", () => {
  it("switches between a new organization and a campus for an existing one", async () => {
    mockApi([]);
    at("/register");
    const nav = screen.getByRole("navigation", { name: "Registration type" });
    expect(within(nav).getByRole("link", { name: /New organization/ }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("main").className).toMatch(/max-w-xl/);
    expect(screen.getByRole("link", { name: "add a campus to your organization" }).getAttribute("href")).toBe("/register/campus");
    fireEvent.click(within(nav).getByRole("link", { name: /Add a campus/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Add a campus to your organization" })).toBeTruthy();
    expect(
      within(screen.getByRole("navigation", { name: "Registration type" }))
        .getByRole("link", { name: /Add a campus/ })
        .getAttribute("aria-current"),
    ).toBe("page");
  });
});

describe("Already Registered Organization? (campus request)", () => {
  it("sends the exact organization details, verifies the email and shows the pending approval", async () => {
    const calls = mockApi([
      { method: "POST", url: /\/auth\/campus-requests$/, status: 201, body: CODE },
      { method: "POST", url: /\/auth\/campus-requests\/a1\/resend-otp$/, body: CODE },
      { method: "POST", url: /\/auth\/campus-requests\/a1\/verify$/, body: PENDING },
    ]);
    at("/register/campus");
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(screen.getByText("Enter the organization's exact registered name.")).toBeTruthy();
    expect(sent(calls, "POST", /campus-requests$/)).toHaveLength(0);

    fill();
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(await screen.findByText(/ha\*\*\*@north\.example\.com/)).toBeTruthy();
    expect(sent(calls, "POST", /campus-requests$/)[0].body).toEqual({
      organization_name: "Lakeside Institute",
      organization_id: "ZHPXMU29711010321",
      campus_name: "North Campus",
      admin_full_name: "Hamza Raza",
      username: "hamza.r",
      email: "hamza@north.example.com",
      phone_number: "+92 321 7654321",
      password: "Northside-pass-2026",
    });

    fireEvent.click(screen.getByRole("button", { name: "Send a new code" }));
    await waitFor(() => expect(sent(calls, "POST", /resend-otp$/)).toHaveLength(1));
    fireEvent.change(screen.getByLabelText(/^Verification code/), { target: { value: "65 43 21" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and send the request" }));
    expect(await screen.findByRole("heading", { name: "Email verified: waiting for approval" })).toBeTruthy();
    expect(sent(calls, "POST", /verify$/)[0].body).toEqual({ otp: "654321" });
    expect(screen.getByText("North Campus", { selector: "strong" })).toBeTruthy();
    expect(screen.getByText(/expires, and its details are deleted/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to sign-in" }).getAttribute("href")).toBe("/login");
  });

  it("shows the server's refusal and keeps the organization name exactly as typed", async () => {
    const calls = mockApi([
      {
        method: "POST",
        url: /\/auth\/campus-requests$/,
        status: 404,
        body: { detail: "No active organization matches this exact name and Organization ID.", code: "ORGANIZATION_NOT_MATCHED" },
      },
    ]);
    at("/register/campus");
    fill({ "Organization name": "lakeside  institute" });
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(await screen.findByText(/No active organization matches/)).toBeTruthy();
    expect((sent(calls, "POST", /campus-requests$/)[0].body as { organization_name: string }).organization_name).toBe(
      "lakeside  institute",
    );
  });

  it("checks the fields like the server", () => {
    const base = {
      organization_name: "Lakeside Institute", organization_id: "ZHPXMU29711010321", campus_name: "North Campus",
      admin_full_name: "Hamza Raza", username: "hamza.r", email: "hamza@north.example.com", phone_number: "+923217654321",
      password: "Northside-pass-2026", confirm: "Northside-pass-2026",
    };
    expect(campusRequestProblems(base)).toEqual({});
    expect(campusRequestProblems({ ...base, organization_id: "ORG-12345678901" })).toEqual({});
    expect(campusRequestProblems({ ...base, organization_id: "ABC123" }).organization_id).toMatch(/6 capital letters/);
    expect(campusRequestProblems({ ...base, campus_name: "--" }).campus_name).toMatch(/letters or digits/);
    expect(campusRequestProblems({ ...base, confirm: "x" }).confirm).toMatch(/do not match/);
    expect(cleanCampusName("  North   Campus ")).toBe("North Campus");
  });
});
