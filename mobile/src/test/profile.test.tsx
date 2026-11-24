import { act, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { useSession } from "../auth/SessionProvider";
import ProfileChangeScreen from "../features/profile/ProfileChangeScreen";
import ProfileScreen from "../features/profile/ProfileScreen";
import SignInScreen from "../features/auth/SignInScreen";
import { SECURE_KEYS } from "../lib/storageKeys";
import { resetRouter } from "./routerMock";
import { alert, FakeEngine, FakeSocket, fail, mockApi, ok, secureStore, signedInRoutes, storeSession, withProviders } from "./utils";
import { Text } from "react-native";

jest.mock("expo-router", () => require("./routerMock").routerModule);

const PROFILE = {
  organization_name: "UCP",
  organization_id: "UCPABC12345678901",
  guard_id: "ABCDEF123456789012345678901",
  full_name: "Gul A",
  username: "gul.a",
  email: "ga@ucp.example.com",
  phone_number: "+923001234567",
  editable_fields: ["email", "phone_number", "password"],
  read_only_fields: ["full_name", "username", "guard_id", "organization_name", "organization_id"],
  campus_name: "Main Campus",
  status: "ACTIVE",
  operational_mode: "STATIONED",
};
const CHANGE = {
  change_id: "c4a9e000-0000-4000-8000-000000000001",
  status: "OTP_REQUIRED",
  email: "ga***@ucp.example.com",
  new_email: "ne***@ucp.example.com",
  delivery: "EMAIL",
  otp_expires_in_seconds: 300,
  resend_available_in_seconds: 60,
  expires_at: "x",
};
const STATUS = {
  websocket_path: "/api/v1/realtime/ws",
  heartbeat_seconds: 25,
  auth_timeout_seconds: 10,
  voice_call_provider: "none",
  voice_calls_available: false,
  push_delivery_mode: "none",
};

beforeEach(() => {
  resetRouter();
  secureStore().clear();
  storeSession("STATIONED");
  FakeSocket.instances = [];
});

function SessionState() {
  const { state } = useSession();
  return <Text testID="session-state">{state.status === "signedOut" ? `out:${state.notice?.code ?? ""}` : state.status}</Text>;
}

describe("Screen 10 — Guard Profile & Settings", () => {
  it("shows the read-only identity and assignment, honest diagnostics, and the alarm test", async () => {
    mockApi(signedInRoutes({ "GET /guard/profile": ok(PROFILE), "GET /realtime/status": ok(STATUS) }));
    const engine = new FakeEngine();
    await render(withProviders(<ProfileScreen />, { engine }));
    expect(await screen.findByText("Guard ID · ABCDEF123456789012345678901")).toBeTruthy();
    expect(screen.getByText("UCP (UCPABC12345678901)")).toBeTruthy();
    expect(screen.getByText("Main Campus")).toBeTruthy();
    expect(await screen.findByText("Admin Block → Ground → Lobby")).toBeTruthy();
    expect(screen.getByText(/cannot change your operational mode or post/)).toBeTruthy();
    expect(await screen.findByText("Not configured on the server")).toBeTruthy();
    expect(screen.getByText("Test")).toBeTruthy(); // push registration reason from the test stub
    await fireEvent.press(screen.getByTestId("alarm-test"));
    await waitFor(() => expect(engine.ringing).toBe(true));
    // The test plays for 3 seconds, then stops by itself.
    await waitFor(() => expect(engine.ringing).toBe(false), { timeout: 6_000 });
    expect(engine.starts).toBe(1);
  }, 15_000);

  it("changes email and password with the current password and both codes; a new password signs out", async () => {
    const api = mockApi(
      signedInRoutes({
        "POST /guard/profile/changes": ok(CHANGE),
        [`POST /guard/profile/changes/${CHANGE.change_id}/verify`]: ok({ status: "SAVED", profile: PROFILE, signed_out: true }),
      }),
    );
    await render(withProviders(<ProfileChangeScreen />, { outside: <SessionState /> }));
    await fireEvent.changeText(await screen.findByTestId("change-email"), "new@ucp.example.com");
    await fireEvent.changeText(screen.getByTestId("change-new-password"), "Another-guard-pass-9");
    await fireEvent.changeText(screen.getByTestId("change-confirm"), "Another-guard-pass-9");
    await fireEvent.press(screen.getByTestId("change-start"));
    expect(await screen.findByText(/Enter your current password/)).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("change-current"), "Guard-pass-2026");
    await fireEvent.press(screen.getByTestId("change-start"));
    expect(await screen.findByText(/code sent to your new email ne\*\*\*@ucp\.example\.com/)).toBeTruthy();
    expect(api.find("POST", "/guard/profile/changes")[0].body).toEqual({
      current_password: "Guard-pass-2026",
      email: "new@ucp.example.com",
      new_password: "Another-guard-pass-9",
      confirm_new_password: "Another-guard-pass-9",
    });
    const [first, second] = screen.getAllByTestId("otp-0");
    await fireEvent.changeText(first, "111111");
    await fireEvent.changeText(second, "222222");
    await fireEvent.press(screen.getByTestId("change-verify"));
    await waitFor(() => expect(screen.getByTestId("session-state").props.children).toBe("out:PASSWORD_CHANGED"));
    expect(api.find("POST", `/guard/profile/changes/${CHANGE.change_id}/verify`)[0].body).toEqual({
      otp: "111111",
      new_email_otp: "222222",
    });
    expect(secureStore().has(SECURE_KEYS.accessToken)).toBe(false);
  });

  it("cancelling a pending change deletes it on the server (nothing changes)", async () => {
    const api = mockApi(
      signedInRoutes({
        "POST /guard/profile/changes": ok({ ...CHANGE, new_email: null }),
        [`DELETE /guard/profile/changes/${CHANGE.change_id}`]: { status: 204 },
      }),
    );
    await render(withProviders(<ProfileChangeScreen />));
    await fireEvent.changeText(await screen.findByTestId("change-phone"), "+92 300 7654321");
    await fireEvent.changeText(screen.getByTestId("change-current"), "Guard-pass-2026");
    await fireEvent.press(screen.getByTestId("change-start"));
    await fireEvent.press(await screen.findByTestId("change-cancel"));
    await waitFor(() => expect(api.find("DELETE", `/guard/profile/changes/${CHANGE.change_id}`)).toHaveLength(1));
  });
});

describe("Forced sign-out", () => {
  it("ASSIGNMENT_CHANGED over the WebSocket stops the alarm, clears the session and explains it on sign-in", async () => {
    mockApi(signedInRoutes({ "GET /alerts": ok([alert()]), "GET /guard/profile": ok(PROFILE), "GET /realtime/status": ok(STATUS) }));
    const engine = new FakeEngine();
    await render(withProviders(<ProfileScreen />, { engine, outside: <SessionState /> }));
    await waitFor(() => expect(engine.ringing).toBe(true));
    const ws = FakeSocket.latest();
    await act(async () => {
      ws.open();
      ws.serverSend({ type: "session_ended", code: "ASSIGNMENT_CHANGED", detail: "Your operational assignment changed." });
    });
    await waitFor(() => expect(screen.getByTestId("session-state").props.children).toBe("out:ASSIGNMENT_CHANGED"));
    expect(engine.ringing).toBe(false);
    expect(secureStore().has(SECURE_KEYS.accessToken)).toBe(false);
  });

  it("the sign-in screen explains ORGANIZATION_INACTIVE after a 403 from any request", async () => {
    mockApi(signedInRoutes({ "GET /alerts": fail(403, "This organization is not active.", "ORGANIZATION_INACTIVE") }));
    function Screen() {
      const { state } = useSession();
      return state.status === "signedOut" ? <SignInScreen /> : null;
    }
    // The alarm's alert query (inside the signed-in part) gets the 403.
    await render(withProviders(<Text>signed in</Text>, { outside: <Screen /> }));
    expect(await screen.findByTestId("session-notice")).toBeTruthy();
    expect(screen.getByText("Organization not active")).toBeTruthy();
  });

  it("sign-out stops the alarm and forgets the token", async () => {
    mockApi(signedInRoutes({ "GET /alerts": ok([alert()]), "GET /guard/profile": ok(PROFILE), "GET /realtime/status": ok(STATUS) }));
    const engine = new FakeEngine();
    await render(withProviders(<ProfileScreen />, { engine, outside: <SessionState /> }));
    await waitFor(() => expect(engine.ringing).toBe(true));
    await fireEvent.press(screen.getByTestId("sign-out"));
    await waitFor(() => expect(screen.getByTestId("session-state").props.children).toBe("out:SIGNED_OUT"));
    expect(engine.ringing).toBe(false);
    expect(secureStore().has(SECURE_KEYS.accessToken)).toBe(false);
  });
});
