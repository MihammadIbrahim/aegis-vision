import { act, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import AlertChatScreen from "../features/chat/AlertChatScreen";
import ChatsScreen from "../features/chat/ChatsScreen";
import IncidentChatScreen from "../features/chat/IncidentChatScreen";
import { mockParams, mockRouter, resetRouter } from "./routerMock";
import { alert, FakeSocket, fail, mockApi, NETWORK, ok, secureStore, signedInRoutes, storeSession, withProviders } from "./utils";

jest.mock("expo-router", () => require("./routerMock").routerModule);

const A = alert({ myState: "RESPONDING", status: "RESPONDING" });
const CASE = {
  id: "c0000000-0000-4000-8000-000000000001",
  reference: "INC-000007",
  title: "Weapon at the lobby",
  description: "",
  status: "UNDER_INVESTIGATION",
  priority: "HIGH",
  occurred_at: A.created_at,
  camera_id: null,
  camera_name: null,
  building_id: null,
  floor_id: null,
  area_id: null,
  location: "Admin Block → Lobby",
  assigned_to: null,
  assigned_name: "Sana A",
  created_by: null,
  created_by_name: "Sana A",
  resolution_note: null,
  closed_at: null,
  created_at: A.created_at,
  updated_at: A.created_at,
  alert_count: 1,
  report_count: 0,
  evidence_count: 0,
};
const msg = (id: string, role: string, name: string, body: string) => ({
  id,
  sender_role: role,
  sender_name: name,
  body,
  created_at: A.created_at,
});

beforeEach(() => {
  resetRouter();
  secureStore().clear();
  storeSession("STATIONED");
  FakeSocket.instances = [];
});

describe("Screen 7 — group chat", () => {
  it("lists case chats and alert chats", async () => {
    mockApi(signedInRoutes({ "GET /alerts": ok([A]), "GET /guard/incidents": ok([CASE]) }));
    await render(withProviders(<ChatsScreen />));
    await fireEvent.press(await screen.findByTestId(`case-${CASE.id}`));
    expect(mockRouter.push).toHaveBeenCalledWith(`/chat/incident/${CASE.id}`);
    await fireEvent.press(screen.getByTestId(`alert-chat-${A.id}`));
    expect(mockRouter.push).toHaveBeenCalledWith(`/chat/alert/${A.id}`);
  });

  it("shows senders with roles, Supervisor directives, sends with client_message_id and refreshes on a real-time signal", async () => {
    mockParams.current = { id: A.id };
    let messages = [
      msg("m1", "SUPERVISOR", "Sana A", "Secure the east wing and confirm."),
      msg("m2", "GUARD", "Bilal", "En route, 2 minutes."),
    ];
    const api = mockApi(
      signedInRoutes({
        "GET /alerts": ok([A]),
        [`GET /alerts/${A.id}`]: ok(A),
        [`GET /alerts/${A.id}/calls/active`]: ok({ voice_calls_available: true, provider: "livekit", call: null }),
        [`GET /alerts/${A.id}/messages`]: () => ok(messages),
        [`POST /alerts/${A.id}/messages`]: (call) => {
          messages = [...messages, msg("m3", "GUARD", "Gul A", (call.body as { body: string }).body)];
          return ok(messages, 201);
        },
      }),
    );
    await render(withProviders(<AlertChatScreen />));
    expect(await screen.findByText("SUPERVISOR DIRECTIVE")).toBeTruthy();
    expect(screen.getByText("Secure the east wing and confirm.")).toBeTruthy();
    expect(screen.getByText("Bilal · Guard")).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("chat-input"), "On my way to the lobby");
    await fireEvent.press(screen.getByTestId("chat-send"));
    expect(await screen.findByText("On my way to the lobby")).toBeTruthy();
    expect(api.find("POST", `/alerts/${A.id}/messages`)[0].body).toEqual({
      body: "On my way to the lobby",
      client_message_id: expect.any(String),
    });
    // A real-time signal (IDs only) makes the app read the messages again.
    const ws = FakeSocket.latest();
    await act(async () => {
      ws.open();
      ws.serverSend({ type: "ready", user_id: "u", role: "GUARD", heartbeat_seconds: 25, token_expires_at: "x" });
    });
    messages = [...messages, msg("m4", "ADMIN", "Maryam", "Police informed.")];
    const before = api.find("GET", `/alerts/${A.id}/messages`).length;
    await act(async () => ws.serverSend({ type: "event", event: "alert.message", alert_id: A.id, at: "t" }));
    expect(await screen.findByText("Police informed.")).toBeTruthy();
    expect(api.find("GET", `/alerts/${A.id}/messages`).length).toBeGreaterThan(before);
  });

  it("keeps an unsent message with a retry when the network fails", async () => {
    mockParams.current = { id: A.id };
    mockApi(
      signedInRoutes({
        "GET /alerts": ok([A]),
        [`GET /alerts/${A.id}`]: ok(A),
        [`GET /alerts/${A.id}/calls/active`]: ok({ voice_calls_available: false, provider: "none", call: null }),
        [`GET /alerts/${A.id}/messages`]: ok([]),
        [`POST /alerts/${A.id}/messages`]: NETWORK,
      }),
    );
    await render(withProviders(<AlertChatScreen />));
    await fireEvent.changeText(await screen.findByTestId("chat-input"), "Need backup");
    await fireEvent.press(screen.getByTestId("chat-send"));
    expect(await screen.findByText("Not sent: no connection. Tap to retry.")).toBeTruthy();
  });

  it("uses the case chat for incidents and says when the Guard is not a participant", async () => {
    mockParams.current = { id: CASE.id };
    const api = mockApi(
      signedInRoutes({
        "GET /guard/incidents": ok([CASE]),
        [`GET /incidents/${CASE.id}/calls/active`]: ok({ voice_calls_available: true, provider: "livekit", call: null }),
        [`GET /incidents/${CASE.id}/messages`]: ok([]),
        [`POST /incidents/${CASE.id}/messages`]: ok({ ...msg("i1", "GUARD", "Gul A", "Arrived"), sender_user_id: null }, 201),
      }),
    );
    await render(withProviders(<IncidentChatScreen />));
    expect(await screen.findByText("INC-000007 · Weapon at the lobby")).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("chat-input"), "Arrived");
    await fireEvent.press(screen.getByTestId("chat-send"));
    await waitFor(() => expect(api.find("POST", `/incidents/${CASE.id}/messages`)[0].body).toEqual({ body: "Arrived" }));
  });

  it("refuses a case the Guard does not take part in", async () => {
    mockParams.current = { id: "c-other" };
    mockApi(signedInRoutes({ "GET /guard/incidents": ok([CASE]), "GET /incidents/c-other/messages": fail(404, "Not found.") }));
    await render(withProviders(<IncidentChatScreen />));
    expect(await screen.findByText("You are not a participant of this case chat.")).toBeTruthy();
  });
});
