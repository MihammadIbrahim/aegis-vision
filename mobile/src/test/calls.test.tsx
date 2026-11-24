import { act, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { Text } from "react-native";

import { NOT_CONFIGURED_NOTICE, useCalls } from "../calls/CallProvider";
import { ActiveCallOverlay, CallButton, IncomingCallCard } from "../calls/CallViews";
import { resetRouter } from "./routerMock";
import { alert, fail, FakeVoice, mockApi, ok, secureStore, signedInRoutes, storeSession, withProviders } from "./utils";

jest.mock("expo-router", () => require("./routerMock").routerModule);

const A = alert({ myState: "RESPONDING", status: "RESPONDING" });
const ME = "aaaaaaaa-0000-4000-8000-000000000001";
function session(overrides: Record<string, unknown> = {}) {
  return {
    id: "ca11ca11-0000-4000-8000-000000000001",
    scope: "ALERT",
    alert_id: A.id,
    incident_id: null,
    status: "ACTIVE",
    media: "AUDIO",
    started_by: "bbbbbbbb-0000-4000-8000-000000000002",
    started_by_name: "Sana A",
    started_by_role: "SUPERVISOR",
    started_at: A.created_at,
    ended_at: null,
    end_reason: null,
    joined_count: 1,
    my_state: "INVITED",
    can_end: false,
    target_title: "Weapon alert · Lobby Cam",
    target_location: "Admin Block → Ground → Lobby",
    participants: [
      { user_id: "bbbbbbbb-0000-4000-8000-000000000002", full_name: "Sana A", role: "SUPERVISOR", state: "JOINED", invited_at: A.created_at, joined_at: A.created_at, left_at: null, is_me: false },
      { user_id: ME, full_name: "Gul A", role: "GUARD", state: "INVITED", invited_at: A.created_at, joined_at: null, left_at: null, is_me: true },
    ],
    ...overrides,
  };
}
const CONNECTION = { provider: "livekit", url: "wss://test.livekit.example", room: "cctv-call-x", identity: ME, token: "room-token", expires_in: 900 };

function Notice() {
  const calls = useCalls();
  return calls.notice ? <Text testID="call-notice">{calls.notice}</Text> : null;
}

beforeEach(() => {
  resetRouter();
  secureStore().clear();
  storeSession("STATIONED");
});

const target = { scope: "ALERT" as const, id: A.id };

describe("In-app voice calls", () => {
  it("explains VOICE_CALL_NOT_CONFIGURED instead of crashing", async () => {
    mockApi(
      signedInRoutes({
        [`GET /alerts/${A.id}/calls/active`]: ok({ voice_calls_available: false, provider: "none", call: null }),
        [`POST /alerts/${A.id}/calls`]: fail(503, "In-app voice calls are not configured on this server yet.", "VOICE_CALL_NOT_CONFIGURED"),
      }),
    );
    await render(withProviders(<><CallButton target={target} /><Notice /></>));
    expect(await screen.findByText("Voice calls are not configured on the server yet.")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("call-button"));
    expect(await screen.findByTestId("call-notice")).toBeTruthy();
    expect(screen.getByText(NOT_CONFIGURED_NOTICE)).toBeTruthy();
  });

  it("does not start a call without native audio (Expo Go) and says why", async () => {
    const api = mockApi(signedInRoutes({ [`GET /alerts/${A.id}/calls/active`]: ok({ voice_calls_available: true, provider: "livekit", call: null }) }));
    const voice = new FakeVoice(false, "Live call audio needs the development build of the Guard app.");
    await render(withProviders(<><CallButton target={target} /><Notice /></>, { voice }));
    await fireEvent.press(await screen.findByTestId("call-button"));
    expect((await screen.findAllByText("Live call audio needs the development build of the Guard app.")).length).toBeGreaterThan(0);
    expect(api.find("POST", `/alerts/${A.id}/calls`)).toHaveLength(0);
  });

  it("starts a call, connects with the server's room token, and offers mute, speaker, participants and leave", async () => {
    const started = session({ my_state: "JOINED", started_by: ME, started_by_name: "Gul A", started_by_role: "GUARD", can_end: true });
    const api = mockApi(
      signedInRoutes({
        [`GET /alerts/${A.id}/calls/active`]: ok({ voice_calls_available: true, provider: "livekit", call: null }),
        [`POST /alerts/${A.id}/calls`]: ok({ outcome: "STARTED", call: started, connection: CONNECTION }, 201),
        [`GET /calls/${started.id}`]: ok(started),
        [`POST /calls/${started.id}/leave`]: ok({ call: { ...started, status: "ENDED" }, call_ended: true }),
      }),
    );
    const voice = new FakeVoice();
    await render(withProviders(<><CallButton target={target} /><ActiveCallOverlay /></>, { voice }));
    await fireEvent.press(await screen.findByTestId("call-button"));
    expect(await screen.findByTestId("active-call")).toBeTruthy();
    expect(voice.connected).toEqual({ url: "wss://test.livekit.example", token: "room-token" });
    expect(screen.getByText("Sana A")).toBeTruthy();
    expect(screen.getByText("Supervisor")).toBeTruthy();
    expect(screen.getByTestId("call-end-all")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("call-mute"));
    expect(voice.microphone).toBe(false);
    await fireEvent.press(screen.getByTestId("call-speaker"));
    expect(voice.speaker).toBe(true);
    await fireEvent.press(screen.getByTestId("call-leave"));
    await waitFor(() => expect(screen.queryByTestId("active-call")).toBeNull());
    expect(api.find("POST", `/calls/${started.id}/leave`)).toHaveLength(1);
    expect(voice.connected).toBeNull();
  });

  it("rings for an invitation, and answers or declines it", async () => {
    let invited = session();
    const api = mockApi(
      signedInRoutes({
        "GET /calls": () => ok([invited]),
        [`POST /calls/${invited.id}/decline`]: () => {
          invited = session({ my_state: "DECLINED" });
          return ok(invited);
        },
        [`POST /calls/${invited.id}/join`]: ok({ outcome: "JOINED", call: session({ my_state: "JOINED" }), connection: CONNECTION }),
        [`GET /calls/${invited.id}`]: ok(session({ my_state: "JOINED" })),
      }),
    );
    const voice = new FakeVoice();
    const view = await render(withProviders(<><IncomingCallCard /><ActiveCallOverlay /></>, { voice }));
    expect(await screen.findByText("Incoming alert call")).toBeTruthy();
    expect(screen.getByText(/Sana A \(Supervisor\)/)).toBeTruthy();
    expect(screen.getByText("Weapon alert · Lobby Cam · Admin Block → Ground → Lobby")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("incoming-accept"));
    expect(await screen.findByTestId("active-call")).toBeTruthy();
    expect(api.find("POST", `/calls/${invited.id}/join`)).toHaveLength(1);
    await view.unmount();

    invited = session();
    await render(withProviders(<IncomingCallCard />, { voice: new FakeVoice() }));
    await fireEvent.press(await screen.findByTestId("incoming-decline"));
    await waitFor(() => expect(screen.queryByTestId("incoming-call")).toBeNull());
    expect(api.find("POST", `/calls/${invited.id}/decline`)).toHaveLength(1);
  });

  it("leaves the call when the server ends it (alert closed)", async () => {
    const joined = session({ my_state: "JOINED" });
    let ended = false;
    mockApi(
      signedInRoutes({
        "GET /calls": () => ok(ended ? [] : [session()]),
        [`POST /calls/${joined.id}/join`]: ok({ outcome: "JOINED", call: joined, connection: CONNECTION }),
        [`GET /calls/${joined.id}`]: () => ok(ended ? { ...joined, status: "ENDED", end_reason: "TARGET_CLOSED" } : joined),
      }),
    );
    const voice = new FakeVoice();
    await render(withProviders(<><IncomingCallCard /><ActiveCallOverlay /></>, { voice }));
    await fireEvent.press(await screen.findByTestId("incoming-accept"));
    await screen.findByTestId("active-call");
    ended = true;
    await act(async () => {
      voice.events?.onParticipantsChanged([]);
    });
    await waitFor(() => expect(screen.queryByTestId("active-call")).toBeNull(), { timeout: 8000 });
    expect(voice.connected).toBeNull();
  }, 15000);
});
