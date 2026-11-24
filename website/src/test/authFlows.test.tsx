// Sign-in redirect and Forgot Password (POST /auth/password-reset…), matched field for field with the Core API
// (password_reset_router.py, supervisor_dto.py).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "../app/App";
import { AuthProvider, StaticAuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";
import { mockApi, sent } from "./helpers";

afterEach(() => sessionStorage.clear());

function signedOut(path: string) {
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

function type(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

const STARTED = {
  reset_id: "r1", status: "OTP_REQUIRED",
  message: "If an Admin or Supervisor account matches these details, a verification code is emailed to its registered address. If no code arrives, request a new one.",
  otp_expires_in_seconds: 300, resend_available_in_seconds: 0, expires_at: "2026-11-13T10:00:00Z",
};

describe("sign-in", () => {
  it("opens the dashboard after the email code", async () => {
    const calls = mockApi([
      { method: "POST", url: /\/auth\/organization-admin\/login$/, body: { login_id: "l1", email: "ma***@lakeside.example.com", otp_expires_in_seconds: 300 } },
      { method: "POST", url: /\/login\/l1\/verify$/, body: { access_token: "t", token_type: "bearer", expires_in: 3600 } },
      { method: "GET", url: /\/auth\/me$/, body: { user_id: "X", full_name: "Admin A", role: "ADMIN", dashboard: null } },
    ]);
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthProvider>
      </MemoryRouter>,
    );
    type(/^Organization name/, "Lakeside Institute");
    type(/^Organization ID/, "ZHPXMU29711010321");
    type(/^Email/, "maryam@lakeside.example.com");
    type(/^Password/, "Lakeside-pass-2026");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    type(await screen.findByLabelText(/^Verification code/).then(() => /^Verification code/), "123456");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("link", { name: "Dashboard", current: "page" }, { timeout: 5000 })).toBeTruthy();
    expect(sent(calls, "POST", /login$/)[0].body).toEqual({
      organization_name: "Lakeside Institute", organization_id: "ZHPXMU29711010321",
      email: "maryam@lakeside.example.com", password: "Lakeside-pass-2026",
    });
  });

  it("links to Forgot password", () => {
    mockApi([]);
    signedOut("/login");
    expect(screen.getByRole("link", { name: "Forgot password?" }).getAttribute("href")).toBe("/forgot-password");
  });
});

describe("forgot password", () => {
  it("sends the Organization ID and email, then the code, then the new password", async () => {
    const calls = mockApi([
      { method: "POST", url: /\/auth\/password-reset$/, status: 202, body: STARTED },
      { method: "POST", url: /\/password-reset\/r1\/resend-otp$/, status: 202, body: STARTED },
      { method: "POST", url: /\/password-reset\/r1\/verify$/, body: { reset_token: "one-time-token", expires_in: 600 } },
      {
        method: "POST",
        url: /\/password-reset\/r1\/complete$/,
        body: { status: "PASSWORD_CHANGED", message: "Your password has been changed. Every earlier session has been signed out. Please sign in again." },
      },
    ]);
    signedOut("/forgot-password");
    expect(screen.getByRole("heading", { level: 1, name: "Reset your password" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(screen.getByText("Enter the Organization ID.")).toBeTruthy();
    expect(sent(calls, "POST", /password-reset$/)).toHaveLength(0);

    type(/^Organization ID/, " zhpxmu29711010321 ");
    type(/^Email/, " maryam@lakeside.example.com ");
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    expect(await screen.findByText(/If an Admin or Supervisor account matches/)).toBeTruthy();
    expect(sent(calls, "POST", /password-reset$/)[0].body).toEqual({
      organization_id: "ZHPXMU29711010321",
      email: "maryam@lakeside.example.com",
    });

    fireEvent.click(screen.getByRole("button", { name: "Send a new code" }));
    await waitFor(() => expect(sent(calls, "POST", /resend-otp$/)).toHaveLength(1));
    await waitFor(() => expect((screen.getByRole("button", { name: "Send a new code" }) as HTMLButtonElement).disabled).toBe(false));
    type(/^Verification code/, "654321");
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));
    expect(await screen.findByRole("form", { name: "Choose a new password" })).toBeTruthy();
    expect(sent(calls, "POST", /verify$/)[0].body).toEqual({ otp: "654321" });

    type(/^New password/, "short1");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(screen.getByText(/12 to 128 characters/)).toBeTruthy();
    type(/^New password/, "Lakeside-new-2026");
    type(/^Confirm new password/, "Lakeside-new-2026");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("heading", { name: "Password changed" })).toBeTruthy();
    expect(sent(calls, "POST", /complete$/)[0].body).toEqual({
      reset_token: "one-time-token",
      new_password: "Lakeside-new-2026",
      confirm_new_password: "Lakeside-new-2026",
    });
    expect(screen.getByRole("link", { name: "Go to sign-in" }).getAttribute("href")).toBe("/login");
  });

  it("can use a Supervisor ID instead, and shows the server's refusal", async () => {
    const calls = mockApi([
      { method: "POST", url: /\/auth\/password-reset$/, status: 202, body: STARTED },
      { method: "POST", url: /\/verify$/, status: 400, body: { detail: "The code is incorrect. 4 attempts left.", code: "OTP_INVALID" } },
    ]);
    signedOut("/forgot-password");
    fireEvent.click(screen.getByLabelText("Use Supervisor ID"));
    type(/^Supervisor ID/, "abcdef123456789012345678901");
    type(/^Email/, "sara@lakeside.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    await screen.findByText(/If an Admin or Supervisor account matches/);
    expect(sent(calls, "POST", /password-reset$/)[0].body).toEqual({
      supervisor_id: "ABCDEF123456789012345678901",
      email: "sara@lakeside.example.com",
    });
    type(/^Verification code/, "000000");
    fireEvent.click(screen.getByRole("button", { name: "Verify code" }));
    expect(await screen.findByText("The code is incorrect. 4 attempts left.")).toBeTruthy();
  });
});

describe("auth top bar", () => {
  it("does not stick over the form while scrolling", () => {
    mockApi([]);
    signedOut("/register");
    const bar = screen.getByRole("link", { name: "AI CCTV Security, home" }).closest("header") as HTMLElement;
    expect(bar.className).not.toMatch(/\bsticky\b/);
  });
});
