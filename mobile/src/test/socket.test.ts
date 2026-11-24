import { RealtimeSocket } from "../realtime/socket";
import { FakeSocket, fakeSocketFactory } from "./utils";

function setup(token: string | null = "tok") {
  FakeSocket.instances = [];
  const handlers = { onStatus: jest.fn(), onEvent: jest.fn(), onSessionEnded: jest.fn(), onReady: jest.fn() };
  const socket = new RealtimeSocket("ws://api/api/v1/realtime/ws", () => token, handlers, fakeSocketFactory, 25_000);
  return { socket, handlers };
}

describe("Real-time socket", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("authenticates in the first message (not the URL), pings, and forwards events", () => {
    const { socket, handlers } = setup();
    socket.start();
    const ws = FakeSocket.latest();
    expect(ws.url).toBe("ws://api/api/v1/realtime/ws");
    ws.open();
    expect(ws.sent[0]).toEqual({ type: "auth", access_token: "tok" });
    ws.serverSend({ type: "ready", user_id: "u", role: "GUARD", heartbeat_seconds: 25, token_expires_at: "x" });
    expect(handlers.onStatus).toHaveBeenLastCalledWith("open");
    expect(handlers.onReady).toHaveBeenCalled();
    jest.advanceTimersByTime(25_000);
    expect(ws.sent[1]).toEqual({ type: "ping" });
    ws.serverSend({ type: "event", event: "alert.assigned", alert_id: "a1", at: "t" });
    expect(handlers.onEvent).toHaveBeenCalledWith(expect.objectContaining({ event: "alert.assigned", alert_id: "a1" }));
    socket.stop();
  });

  it("ends the session on session_ended ASSIGNMENT_CHANGED and does not reconnect", () => {
    const { socket, handlers } = setup();
    socket.start();
    const ws = FakeSocket.latest();
    ws.open();
    ws.serverSend({ type: "session_ended", code: "ASSIGNMENT_CHANGED", detail: "changed" });
    ws.serverClose(4401);
    expect(handlers.onSessionEnded).toHaveBeenCalledWith("ASSIGNMENT_CHANGED", "changed");
    jest.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("treats a bare 4403 close as an inactive organization", () => {
    const { socket, handlers } = setup();
    socket.start();
    FakeSocket.latest().serverClose(4403);
    expect(handlers.onSessionEnded).toHaveBeenCalledWith("ORGANIZATION_INACTIVE", "");
  });

  it("reconnects with back-off after a dropped connection (polling covers the gap)", () => {
    const { socket, handlers } = setup();
    socket.start();
    FakeSocket.latest().serverClose(1006);
    expect(handlers.onStatus).toHaveBeenLastCalledWith("reconnecting");
    jest.advanceTimersByTime(1_000);
    expect(FakeSocket.instances).toHaveLength(2);
    socket.stop();
  });

  it("does not connect without a token", () => {
    const { socket } = setup(null);
    socket.start();
    expect(FakeSocket.instances).toHaveLength(0);
  });
});
