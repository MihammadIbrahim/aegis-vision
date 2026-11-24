import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import AlertDetailScreen from "../features/alerts/AlertDetailScreen";
import DashboardScreen from "../features/alerts/DashboardScreen";
import { mockParams, mockRouter, resetRouter } from "./routerMock";
import { alert, fail, mockApi, ok, secureStore, setOnline, signedInRoutes, storeSession, withProviders } from "./utils";

jest.mock("expo-router", () => require("./routerMock").routerModule);

beforeEach(() => {
  resetRouter();
  secureStore().clear();
  setOnline(true);
});

describe("Screen 5 — dual dashboards", () => {
  it("Stationed Monitoring Dashboard shows the post and the alerts routed there", async () => {
    storeSession("STATIONED");
    mockApi(signedInRoutes({ "GET /alerts": ok([alert({ myState: "ACKNOWLEDGED", status: "ACKNOWLEDGED" })]) }));
    await render(withProviders(<DashboardScreen />));
    expect(await screen.findByText("Stationed Monitoring Dashboard")).toBeTruthy();
    expect(await screen.findByText("Your post: Admin Block → Ground → Lobby")).toBeTruthy();
    expect(await screen.findByText("Weapon Detection")).toBeTruthy();
    expect(screen.getByText("HIGH PRIORITY")).toBeTruthy();
    expect(screen.getByText("Lobby Cam · Admin Block → Ground → Lobby")).toBeTruthy();
  });

  it("Standby Emergency Dashboard explains escalations and assistance broadcasts", async () => {
    storeSession("STANDBY");
    mockApi(signedInRoutes({}, "STANDBY"));
    await render(withProviders(<DashboardScreen />));
    expect(await screen.findByText("Standby Emergency Dashboard")).toBeTruthy();
    expect(screen.getByText(/campus-wide escalations, assistance requests and rapid response/)).toBeTruthy();
    expect(await screen.findByText(/receive alerts when they are escalated to you or a Guard asks for assistance/)).toBeTruthy();
  });

  it("filters Active / New, My Responding and Resolved / History, and opens details", async () => {
    storeSession("STATIONED");
    mockApi(
      signedInRoutes({
        "GET /alerts": ok([
          alert({ id: "new-1", myState: "ACKNOWLEDGED", status: "ACKNOWLEDGED", alert_type: "WEAPON" }),
          alert({ id: "resp-1", myState: "RESPONDING", status: "RESPONDING", alert_type: "FIGHT_VIOLENCE" }),
          alert({ id: "done-1", myState: "RESPONDING", status: "RESOLVED", alert_type: "RESTRICTED_AREA", priority: "STANDARD" }),
        ]),
      }),
    );
    await render(withProviders(<DashboardScreen />));
    expect(await screen.findByTestId("alert-card-new-1")).toBeTruthy();
    expect(screen.queryByTestId("alert-card-resp-1")).toBeNull();
    await fireEvent.press(screen.getByTestId("tab-RESPONDING"));
    expect(screen.getByTestId("alert-card-resp-1")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("tab-HISTORY"));
    expect(screen.getByTestId("alert-card-done-1")).toBeTruthy();
    expect(screen.getByText("STANDARD PRIORITY")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("alert-card-done-1"));
    expect(mockRouter.push).toHaveBeenCalledWith("/alert/done-1");
  });

  it("says real-time server alerts are paused while offline", async () => {
    storeSession("STATIONED");
    mockApi(signedInRoutes());
    setOnline(false);
    await render(withProviders(<DashboardScreen />));
    expect(await screen.findByTestId("offline-banner")).toBeTruthy();
    expect(screen.getByText(/Real-time server alerts are paused while you are offline/)).toBeTruthy();
    expect(screen.getByTestId("offline-pill")).toBeTruthy();
  });
});

describe("Screen 6 — alert details and actions", () => {
  const A = alert({ myState: "ACKNOWLEDGED", status: "ACKNOWLEDGED", has_snapshot: true });
  const routes = (extra = {}) =>
    signedInRoutes({
      "GET /alerts": ok([A]),
      [`GET /alerts/${A.id}`]: ok(A),
      [`GET /alerts/${A.id}/responders`]: ok([
        { full_name: "Bilal", state: "RESPONDING", assessment: null, reason: "ESCALATION", acknowledged_at: null, responding_at: A.created_at, is_me: false },
      ]),
      [`GET /alerts/${A.id}/history`]: ok([
        { event_type: "CREATED", actor_role: "SYSTEM", actor_name: null, from_status: null, to_status: "NEW", details: {}, created_at: A.created_at },
      ]),
      [`GET /alerts/${A.id}/calls/active`]: ok({ voice_calls_available: true, provider: "livekit", call: null }),
      ...extra,
    });

  beforeEach(() => {
    storeSession("STATIONED");
    mockParams.current = { id: A.id };
  });

  it("shows metadata, the responder roster, history, and says snapshots are staff-only", async () => {
    mockApi(routes());
    await render(withProviders(<AlertDetailScreen />));
    expect(await screen.findByText("Admin Block → Ground → Lobby")).toBeTruthy();
    expect(screen.getByText("91%")).toBeTruthy();
    expect(screen.getByText(/shown to Admins and Supervisors only; Guards have no access to video/)).toBeTruthy();
    expect(await screen.findByText("Bilal")).toBeTruthy();
    expect(await screen.findByText(/Alert created · System/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("open-chat"));
    expect(mockRouter.push).toHaveBeenCalledWith(`/chat/alert/${A.id}`);
  });

  it("sends Under Control, Additional Guards Required and I'm Responding with client_action_id", async () => {
    const api = mockApi(
      routes({
        [`POST /alerts/${A.id}/assessment`]: ok({ alert_id: A.id, outcome: "DONE", status: "ACKNOWLEDGED" }),
        [`POST /alerts/${A.id}/responding`]: ok({ alert_id: A.id, outcome: "DONE", status: "RESPONDING" }),
      }),
    );
    await render(withProviders(<AlertDetailScreen />));
    await fireEvent.press(await screen.findByTestId("action-additional"));
    await waitFor(() => expect(api.find("POST", `/alerts/${A.id}/assessment`)).toHaveLength(1));
    expect(api.find("POST", `/alerts/${A.id}/assessment`)[0].body).toEqual({
      assessment: "ADDITIONAL_GUARDS_REQUIRED",
      client_action_id: expect.any(String),
    });
    await fireEvent.press(screen.getByTestId("action-responding"));
    await waitFor(() => expect(api.find("POST", `/alerts/${A.id}/responding`)).toHaveLength(1));
  });

  it("requires acknowledgement before an assessment, and shows server refusals", async () => {
    const unacked = alert();
    mockApi(
      routes({
        "GET /alerts": ok([unacked]),
        [`GET /alerts/${A.id}`]: ok(unacked),
        [`POST /alerts/${A.id}/acknowledge`]: fail(409, "This alert is closed.", "ALERT_CLOSED"),
      }),
    );
    await render(withProviders(<AlertDetailScreen />, { engine: { ringing: false, start: async () => undefined, stop: async () => undefined } }));
    expect(await screen.findByText("Acknowledge first, then report your assessment.")).toBeTruthy();
    expect(screen.getByTestId("action-under-control").props.accessibilityState).toMatchObject({ disabled: true });
    await fireEvent.press(screen.getByTestId("action-acknowledge"));
    expect(await screen.findByTestId("action-error")).toBeTruthy();
  });
});
