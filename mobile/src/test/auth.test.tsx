import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { useEffect } from "react";

import { useAuthFlow } from "../auth/AuthFlowProvider";
import ForgotPasswordScreen from "../features/auth/ForgotPasswordScreen";
import ResetPasswordScreen from "../features/auth/ResetPasswordScreen";
import SignInScreen from "../features/auth/SignInScreen";
import VerifyScreen from "../features/auth/VerifyScreen";
import { SECURE_KEYS } from "../lib/storageKeys";
import { mockRouter, resetRouter } from "./routerMock";
import { fail, mockApi, ok, secureStore, withProviders } from "./utils";

jest.mock("expo-router", () => require("./routerMock").routerModule);

const GUARD_ID = "ABCDEF123456789012345678901";
const SENT = {
  login_id: "11111111-1111-4111-8111-111111111111",
  status: "OTP_REQUIRED",
  email: "ga***@ucp.example.com",
  delivery: "EMAIL",
  otp_expires_in_seconds: 300,
  resend_available_in_seconds: 60,
  login_expires_at: "2026-11-19T10:15:00Z",
};

beforeEach(() => {
  resetRouter();
  secureStore().clear();
});

async function fillSignIn(mode: "STANDBY" | "STATIONED" = "STATIONED") {
  await fireEvent.press(screen.getByTestId(`mode-${mode}`));
  await fireEvent.changeText(screen.getByTestId("input-guard-id"), GUARD_ID.toLowerCase());
  await fireEvent.changeText(screen.getByTestId("input-email"), " ga@ucp.example.com ");
  await fireEvent.changeText(screen.getByTestId("input-password"), "Guard-pass-2026");
}

describe("Screen 1 — Guard Login", () => {
  it("has no organization name field and no registration, and requires the operational mode", async () => {
    mockApi({});
    await render(withProviders(<SignInScreen />, { signedIn: false }));
    expect(screen.queryByText(/organization name/i)).toBeNull();
    expect(screen.queryByText(/\bregister\b|sign up|create (an )?account/i)).toBeNull();
    await fireEvent.press(screen.getByTestId("sign-in"));
    expect(await screen.findByText("Choose Standby or Stationed / Standalone.")).toBeTruthy();
  });

  it("sends exactly {operational_mode, guard_id, email, password} and opens the code screen", async () => {
    const api = mockApi({ "POST /auth/guard/login": ok(SENT) });
    await render(withProviders(<SignInScreen />, { signedIn: false }));
    await fillSignIn("STATIONED");
    await fireEvent.press(screen.getByTestId("sign-in"));
    await waitFor(() => expect(mockRouter.push).toHaveBeenCalledWith("/verify"));
    expect(api.find("POST", "/auth/guard/login")[0].body).toEqual({
      operational_mode: "STATIONED",
      guard_id: GUARD_ID,
      email: "ga@ucp.example.com",
      password: "Guard-pass-2026",
    });
    expect(api.calls[0].headers.Authorization).toBeUndefined();
  });

  it("shows the server's OPERATIONAL_MODE_MISMATCH message naming the right mode", async () => {
    const message = "You are stationed at a post. Choose Stationed / Standalone to sign in.";
    mockApi({ "POST /auth/guard/login": fail(409, message, "OPERATIONAL_MODE_MISMATCH") });
    await render(withProviders(<SignInScreen />, { signedIn: false }));
    await fillSignIn("STANDBY");
    await fireEvent.press(screen.getByTestId("sign-in"));
    expect((await screen.findAllByText(message)).length).toBeGreaterThan(0);
    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it("explains invalid credentials and the 15-minute lockout", async () => {
    let attempt = 0;
    mockApi({
      "POST /auth/guard/login": () =>
        ++attempt === 1
          ? fail(401, "The organization details, email address or password are incorrect.", "INVALID_CREDENTIALS")
          : fail(429, "Too many failed sign-in attempts.", "ACCOUNT_LOCKED", { retry_after_seconds: 900 }),
    });
    await render(withProviders(<SignInScreen />, { signedIn: false }));
    await fillSignIn();
    await fireEvent.press(screen.getByTestId("sign-in"));
    expect(await screen.findByText(/Guard ID, email or password is incorrect/)).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("input-password"), "again");
    await fireEvent.press(screen.getByTestId("sign-in"));
    expect(await screen.findByText(/locked for about 15 minutes/)).toBeTruthy();
  });

  it("remembers the Guard ID, email and mode (never the password) only when asked", async () => {
    mockApi({ "POST /auth/guard/login": ok(SENT) });
    await render(withProviders(<SignInScreen />, { signedIn: false }));
    await fillSignIn();
    await fireEvent(screen.getByTestId("remember-switch"), "valueChange", true);
    await fireEvent.press(screen.getByTestId("sign-in"));
    await waitFor(() => expect(secureStore().get(SECURE_KEYS.rememberedIdentifier)).toBeDefined());
    const saved = JSON.parse(secureStore().get(SECURE_KEYS.rememberedIdentifier) as string);
    expect(saved).toEqual({ guard_id: GUARD_ID, email: "ga@ucp.example.com", operational_mode: "STATIONED" });
    expect(JSON.stringify(saved)).not.toContain("Guard-pass");
  });
});

function WithPendingLogin({ mode }: { mode: "STANDBY" | "STATIONED" }) {
  const { pendingLogin, setPendingLogin } = useAuthFlow();
  useEffect(() => setPendingLogin({ sent: SENT as never, mode }), [setPendingLogin, mode]);
  return pendingLogin ? <VerifyScreen /> : null;
}

async function typeCode(code: string) {
  await fireEvent.changeText(screen.getByTestId("otp-0"), code);
}

describe("Screen 2 — Email OTP Verification", () => {
  it("shows the masked email and a 60-second resend countdown", async () => {
    mockApi({});
    await render(withProviders(<WithPendingLogin mode="STANDBY" />, { signedIn: false }));
    expect(await screen.findByText(/ga\*\*\*@ucp\.example\.com/)).toBeTruthy();
    expect(screen.getByText(/Resend code in 1:00|Resend code in 0:60/)).toBeTruthy();
  });

  it("verifies {otp, operational_mode}, stores the token only in secure storage and opens the dashboard", async () => {
    const api = mockApi({
      "POST /auth/guard/login/11111111-1111-4111-8111-111111111111/verify": ok({
        access_token: "jwt-token",
        token_type: "bearer",
        expires_in: 1800,
        operational_mode: "STANDBY",
        dashboard: "STANDBY_EMERGENCY",
      }),
    });
    await render(withProviders(<WithPendingLogin mode="STANDBY" />, { signedIn: false }));
    await typeCode("123456");
    await fireEvent.press(await screen.findByTestId("verify"));
    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/alerts"));
    expect(api.calls[0].body).toEqual({ otp: "123456", operational_mode: "STANDBY" });
    expect(JSON.parse(secureStore().get(SECURE_KEYS.accessToken) as string)).toBe("jwt-token");
    expect(JSON.parse(secureStore().get(SECURE_KEYS.session) as string)).toMatchObject({ dashboard: "STANDBY_EMERGENCY" });
  });

  it("explains a wrong or expired code and clears the boxes", async () => {
    mockApi({
      "POST /auth/guard/login/11111111-1111-4111-8111-111111111111/verify": fail(
        400,
        "The code is incorrect. 4 attempts remaining.",
        "INVALID_OTP",
      ),
    });
    await render(withProviders(<WithPendingLogin mode="STATIONED" />, { signedIn: false }));
    await typeCode("000000");
    await fireEvent.press(await screen.findByTestId("verify"));
    expect(await screen.findByText(/The code is incorrect\. 4 attempts remaining\./)).toBeTruthy();
    expect(secureStore().has(SECURE_KEYS.accessToken)).toBe(false);
  });
});

describe("Screens 3–4 — Forgot and Reset Password", () => {
  function Flow() {
    const { pendingReset } = useAuthFlow();
    return pendingReset ? <ResetPasswordScreen /> : <ForgotPasswordScreen />;
  }

  it("asks for mode + Guard ID + email, verifies the code, keeps the reset token in memory and resets", async () => {
    const reset = "22222222-2222-4222-8222-222222222222";
    const api = mockApi({
      "POST /auth/password-reset": ok(
        {
          reset_id: reset,
          status: "OTP_REQUIRED",
          message: "If an account matches these details, a verification code is emailed to its registered address.",
          otp_expires_in_seconds: 300,
          resend_available_in_seconds: 60,
          expires_at: "x",
        },
        202,
      ),
      [`POST /auth/password-reset/${reset}/verify`]: ok({ reset_token: "one-time-token", expires_in: 600 }),
      [`POST /auth/password-reset/${reset}/complete`]: ok({ status: "PASSWORD_CHANGED", message: "Changed." }),
    });
    await render(withProviders(<Flow />, { signedIn: false }));
    await fireEvent.press(screen.getByTestId("mode-STANDBY"));
    await fireEvent.changeText(screen.getByTestId("reset-guard-id"), GUARD_ID);
    await fireEvent.changeText(screen.getByTestId("reset-email"), "ga@ucp.example.com");
    await fireEvent.press(screen.getByTestId("send-reset"));
    expect(await screen.findByText(/emailed to ga\*\*\*@ucp\.example\.com/)).toBeTruthy();
    expect(api.calls[0].body).toEqual({ guard_id: GUARD_ID, email: "ga@ucp.example.com", operational_mode: "STANDBY" });

    await typeCode("654321");
    await fireEvent.press(screen.getByTestId("verify-reset"));
    await fireEvent.changeText(await screen.findByTestId("new-password"), "short1");
    expect(screen.getByText(/12 to 128 characters/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("complete-reset"));
    expect(await screen.findByText(/does not meet every rule/)).toBeTruthy();

    await fireEvent.changeText(screen.getByTestId("new-password"), "New-guard-pass-2027");
    await fireEvent.changeText(screen.getByTestId("confirm-password"), "New-guard-pass-2027");
    await fireEvent.press(screen.getByTestId("complete-reset"));
    expect(await screen.findByTestId("reset-done")).toBeTruthy();
    expect(api.find("POST", `/auth/password-reset/${reset}/complete`)[0].body).toEqual({
      reset_token: "one-time-token",
      new_password: "New-guard-pass-2027",
      confirm_new_password: "New-guard-pass-2027",
    });
    // The one-time token never touches the device's storage.
    expect(Array.from(secureStore().values()).join()).not.toContain("one-time-token");
    await fireEvent.press(screen.getByTestId("back-to-sign-in"));
    expect(mockRouter.replace).toHaveBeenCalledWith("/sign-in");
  });
});
