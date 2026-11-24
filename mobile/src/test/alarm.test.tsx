import { act, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { AlarmOverlay } from "../alarm/AlarmOverlay";
import { SECURE_KEYS } from "../lib/storageKeys";
import { resetRouter } from "./routerMock";
import { alert, FakeEngine, fail, mockApi, NETWORK, ok, secureStore, signedInRoutes, storeSession, withProviders } from "./utils";

jest.mock("expo-router", () => require("./routerMock").routerModule);

const A = alert();
const RESULT = { alert_id: A.id, outcome: "DONE", status: "ACKNOWLEDGED" };

beforeEach(() => {
  resetRouter();
  secureStore().clear();
  storeSession("STATIONED");
});

describe("Persistent alert alarm", () => {
  it("rings and covers the screen for an unacknowledged alert; back and 'view details' do not silence it", async () => {
    mockApi(signedInRoutes({ "GET /alerts": ok([A]) }));
    const engine = new FakeEngine();
    await render(withProviders(<AlarmOverlay />, { engine }));
    expect(await screen.findByTestId("alarm-modal")).toBeTruthy();
    expect(screen.getByText("UNACKNOWLEDGED SECURITY ALERT")).toBeTruthy();
    expect(screen.getByText("Weapon Detection")).toBeTruthy();
    expect(screen.getByText("Admin Block → Ground → Lobby")).toBeTruthy();
    expect(engine.ringing).toBe(true);
    // Android back: the modal's onRequestClose does nothing.
    await fireEvent(screen.getByTestId("alarm-modal-root"), "requestClose");
    expect(screen.getByTestId("alarm-modal")).toBeTruthy();
    expect(engine.ringing).toBe(true);
    await fireEvent.press(screen.getByTestId("alarm-details"));
    expect(await screen.findByTestId("alarm-banner")).toBeTruthy();
    expect(engine.ringing).toBe(true);
    expect(engine.stops).toBe(0);
  });

  it("keeps ringing when the server has not confirmed (offline), and stops only after it confirms", async () => {
    let online = false;
    const api = mockApi(
      signedInRoutes({
        "GET /alerts": () => ok([online ? alert({ myState: "ACKNOWLEDGED", status: "ACKNOWLEDGED" }) : A]),
        [`POST /alerts/${A.id}/acknowledge`]: () => (online ? ok(RESULT) : NETWORK),
      }),
    );
    const engine = new FakeEngine();
    await render(withProviders(<AlarmOverlay />, { engine }));
    await fireEvent.press(await screen.findByTestId("alarm-primary"));
    expect(await screen.findByText(/Not confirmed: no connection to the server/)).toBeTruthy();
    expect(engine.ringing).toBe(true);
    online = true;
    await fireEvent.press(screen.getByTestId("alarm-primary"));
    await waitFor(() => expect(engine.ringing).toBe(false));
    expect(screen.queryByTestId("alarm-modal")).toBeNull();
    const posted = api.find("POST", `/alerts/${A.id}/acknowledge`);
    expect(posted[1].body).toEqual({ client_action_id: expect.any(String) });
  });

  it("offers I'm Responding first for an escalated or assistance alert", async () => {
    const escalated = alert({ reason: "ASSISTANCE", status: "ESCALATED" });
    const api = mockApi(
      signedInRoutes({
        "GET /alerts": ok([escalated]),
        [`POST /alerts/${A.id}/responding`]: ok({ ...RESULT, status: "ESCALATED" }),
      }),
    );
    const engine = new FakeEngine();
    await render(withProviders(<AlarmOverlay />, { engine }));
    expect(await screen.findByText("Assistance requested by a Guard")).toBeTruthy();
    expect(screen.getByText("I'm Responding")).toBeTruthy();
    expect(screen.getByTestId("alarm-acknowledge")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("alarm-primary"));
    await waitFor(() => expect(engine.ringing).toBe(false));
    expect(api.find("POST", `/alerts/${A.id}/responding`)).toHaveLength(1);
  });

  it("stops when the alert is closed on the server, and never rings for acknowledged or closed alerts", async () => {
    let alerts = [A];
    mockApi(signedInRoutes({ "GET /alerts": () => ok(alerts) }));
    const engine = new FakeEngine();
    await render(withProviders(<AlarmOverlay />, { engine }));
    await screen.findByTestId("alarm-modal");
    alerts = [alert({ status: "RESOLVED", closed_at: new Date().toISOString() }), alert({ id: "x2", myState: "RESPONDING" })];
    await act(async () => {
      await require("@tanstack/react-query");
    });
    // Next poll (5 s while the WebSocket is not connected).
    await waitFor(() => expect(engine.ringing).toBe(false), { timeout: 8000 });
  }, 15000);

  it("is silenced by a forced sign-out (ASSIGNMENT_CHANGED), which also clears the encrypted session", async () => {
    let revoked = false;
    mockApi(
      signedInRoutes({
        "GET /alerts": () => (revoked ? fail(401, "Your operational assignment changed. Please sign in again.", "ASSIGNMENT_CHANGED") : ok([A])),
      }),
    );
    const engine = new FakeEngine();
    await render(withProviders(<AlarmOverlay />, { engine }));
    await screen.findByTestId("alarm-modal");
    revoked = true;
    await waitFor(() => expect(engine.ringing).toBe(false), { timeout: 8000 });
    expect(secureStore().has(SECURE_KEYS.accessToken)).toBe(false);
    expect(secureStore().has(SECURE_KEYS.session)).toBe(false);
  }, 15000);
});
