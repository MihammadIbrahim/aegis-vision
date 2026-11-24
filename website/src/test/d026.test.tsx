// Decision D-026: real-time messaging and in-app voice calls for Admins and Supervisors (the web side of the Guard
// app's D-025). The WebSocket protocol, live chat and page refreshes, the connection indicator, incoming-call ringing,
// the call dock (mute, deafen, leave, end for everyone), honest notices (VOICE_CALL_NOT_CONFIGURED, microphone denied,
// no WebRTC) and session endings. Field names are the Core API's (realtime_router.py, call_dto.py).
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, onUnauthorized, setToken } from "../api/client";
import type { Role } from "../api/types";
import { App } from "../app/App";
import { AuthProvider, StaticAuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";
import type { Alert, AlertMessage } from "../features/alerts";
import { AlertDetailPage } from "../features/alerts/pages/AlertDetailPage";
import { CallProvider, NOT_CONFIGURED_NOTICE } from "../features/calls/CallProvider";
import { CallDock, IncomingCallBanner } from "../features/calls/components/CallViews";
import type { CallJoin, CallParticipant, CallSession } from "../features/calls/types";
import { browserVoiceSupport, microphoneMessage } from "../features/calls/voice";
import type { IncidentDetail, IncidentMessage } from "../features/incidents/api";
import { IncidentDetailPage } from "../features/incidents/pages/IncidentDetailPage";
import { NotificationBell } from "../features/notifications";
import { ConnectionStatus } from "../realtime/ConnectionStatus";
import { RealtimeProvider, useLiveRefresh } from "../realtime/RealtimeProvider";
import { RealtimeSocket, realtimeUrl } from "../realtime/socket";
import type { RealtimeEvent, SocketStatus } from "../realtime/types";
import { mockApi, sent, sessionFor } from "./helpers";
import { FakeRinger, FakeSocket, FakeVoice } from "./realtimeFakes";

const TOKEN = "web-token-1";

afterEach(() => {
  vi.useRealTimers();
  onUnauthorized(null);
});

// --- Fixtures --------------------------------------------------------------------------------------------------------

function alert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: "al1",
    alert_type: "WEAPON",
    priority: "HIGH",
    status: "NEW",
    camera_id: "c1",
    camera_name: "Gate Camera",
    location: {
      building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR",
      floor_id: "f1", floor_name: "Ground", area_id: "a1", area_name: "Main Gate",
    },
    confidence: "0.9100",
    detected_at: "2026-11-19T10:00:00Z",
    last_detected_at: "2026-11-19T10:02:00Z",
    detection_count: 3,
    close_note: null,
    closed_at: null,
    created_at: "2026-11-19T10:00:00Z",
    updated_at: "2026-11-19T10:02:00Z",
    recipients: [
      { guard_id: "GRDABC1", full_name: "Bilal Guard", reason: "LOCATION", state: "SENT", assessment: null, acknowledged_at: null, responding_at: null },
    ],
    ...overrides,
  };
}

function person(user_id: string, full_name: string, role: CallParticipant["role"], state: CallParticipant["state"], is_me = false): CallParticipant {
  return { user_id, full_name, role, state, invited_at: "2026-11-19T10:05:00Z", joined_at: state === "JOINED" ? "2026-11-19T10:05:00Z" : null, left_at: null, is_me };
}

function callSession(overrides: Partial<CallSession> = {}): CallSession {
  return {
    id: "c1",
    scope: "ALERT",
    alert_id: "al1",
    incident_id: null,
    status: "ACTIVE",
    media: "AUDIO",
    started_by: "g1",
    started_by_name: "Bilal Guard",
    started_by_role: "GUARD",
    started_at: "2026-11-19T10:05:00Z",
    ended_at: null,
    end_reason: null,
    joined_count: 1,
    my_state: "INVITED",
    can_end: true,
    participants: [person("g1", "Bilal Guard", "GUARD", "JOINED"), person("X", "Supervisor S", "SUPERVISOR", "INVITED", true)],
    target_title: "Weapon alert · Gate Camera",
    target_location: "Admin Block → Ground → Main Gate",
    ...overrides,
  };
}

const CONNECTION = { provider: "livekit" as const, url: "wss://voice.example.test", room: "cctv-call-c1", identity: "X", token: "lk-token", expires_in: 600 };

function joinedCall(overrides: Partial<CallSession> = {}): CallJoin {
  return {
    outcome: "JOINED",
    call: callSession({
      my_state: "JOINED",
      joined_count: 2,
      participants: [person("g1", "Bilal Guard", "GUARD", "JOINED"), person("X", "Supervisor S", "SUPERVISOR", "JOINED", true)],
      ...overrides,
    }),
    connection: CONNECTION,
  };
}

const NO_CALL = { voice_calls_available: true, provider: "livekit", call: null };

function alertRoutes(a: Alert, thread: AlertMessage[] = []) {
  return [
    { method: "GET", url: /\/alerts\/al1$/, body: a },
    { method: "GET", url: /\/alerts\/al1\/history$/, body: [] },
    { method: "GET", url: /\/alerts\/al1\/messages$/, body: thread },
  ];
}

function incidentDetail(): IncidentDetail {
  return {
    incident: {
      id: "i1", reference: "INC-000007", title: "Weapon at the lobby", description: "", status: "OPEN", priority: "HIGH",
      occurred_at: "2026-11-19T10:00:00Z", camera_id: null, camera_name: null, building_id: null, floor_id: null, area_id: null,
      location: null, assigned_to: null, assigned_name: null, created_by: "X", created_by_name: "Supervisor S", resolution_note: null,
      closed_at: null, created_at: "2026-11-19T10:00:00Z", updated_at: "2026-11-19T10:00:00Z", alert_count: 0, report_count: 0, evidence_count: 0,
    },
    alerts: [], reports: [], evidence: [], participants: [], footage_gaps: [], history: [], person_search: [], ai_assistance_available: false,
  };
}

// --- Rendering inside the signed-in shell's providers -----------------------------------------------------------------

interface ShellOptions {
  role?: Role;
  path?: string;
  route?: string;
  voice?: FakeVoice;
  ringer?: FakeRinger;
  signOut?: (code?: string) => void;
  beforeSignOut?: (hook: () => void) => () => void;
}

function shell(ui: ReactElement, o: ShellOptions = {}) {
  sessionStorage.setItem("cctv.accessToken", TOKEN);
  const content = o.route ? (
    <Routes>
      <Route path={o.route} element={ui} />
    </Routes>
  ) : (
    ui
  );
  return render(
    <MemoryRouter initialEntries={[o.path ?? "/"]}>
      <StaticAuthProvider session={sessionFor(o.role ?? "SUPERVISOR")} signOut={o.signOut} beforeSignOut={o.beforeSignOut}>
        <ToastProvider>
          <RealtimeProvider>
            <CallProvider voice={o.voice ?? new FakeVoice()} ringer={o.ringer ?? new FakeRinger()}>
              <ConnectionStatus />
              {content}
              <IncomingCallBanner />
              <CallDock />
            </CallProvider>
          </RealtimeProvider>
        </ToastProvider>
      </StaticAuthProvider>
    </MemoryRouter>,
  );
}

/** Opens the page's WebSocket (auth → ready) and waits for the "Live" indicator. */
async function live(): Promise<FakeSocket> {
  await waitFor(() => expect(FakeSocket.latest()).toBeDefined());
  const socket = FakeSocket.latest() as FakeSocket;
  act(() => socket.ready());
  await screen.findByText("Live");
  return socket;
}

function pause(ms: number) {
  return act(() => new Promise((resolve) => setTimeout(resolve, ms)));
}

const MINE = /\/api\/v1\/calls$/;

// --- The WebSocket client ---------------------------------------------------------------------------------------------

describe("real-time WebSocket client", () => {
  function client(token: string | null = TOKEN) {
    const statuses: SocketStatus[] = [];
    const events: RealtimeEvent[] = [];
    const ended: string[] = [];
    let readies = 0;
    const socket = new RealtimeSocket(
      "ws://localhost:5173/api/v1/realtime/ws",
      () => token,
      { onStatus: (s) => statuses.push(s), onEvent: (e) => events.push(e), onSessionEnded: (code) => ended.push(code), onReady: () => (readies += 1) },
      (url) => new FakeSocket(url),
    );
    return { socket, statuses, events, ended, readies: () => readies };
  }

  it("authenticates with the first message, never in the URL, and passes signals on", () => {
    const c = client();
    c.socket.start();
    const fake = FakeSocket.latest() as FakeSocket;
    expect(fake.url).toBe("ws://localhost:5173/api/v1/realtime/ws");
    expect(fake.url).not.toContain(TOKEN);
    fake.open();
    expect(fake.sent).toEqual([{ type: "auth", access_token: TOKEN }]);
    fake.serverSend({ type: "ready", user_id: "X", role: "SUPERVISOR", heartbeat_seconds: 25, token_expires_at: "x" });
    expect(c.statuses).toEqual(["connecting", "open"]);
    expect(c.readies()).toBe(1);
    fake.event("alert.message", { alert_id: "al1" });
    expect(c.events).toMatchObject([{ type: "event", event: "alert.message", alert_id: "al1" }]);
    c.socket.stop();
  });

  it("pings every heartbeat", () => {
    vi.useFakeTimers();
    const c = client();
    c.socket.start();
    const fake = FakeSocket.latest() as FakeSocket;
    fake.ready();
    vi.advanceTimersByTime(25_000);
    expect(fake.sent.filter((m) => m.type === "ping")).toHaveLength(1);
    vi.advanceTimersByTime(25_000);
    expect(fake.sent.filter((m) => m.type === "ping")).toHaveLength(2);
    c.socket.stop();
  });

  it("reconnects with exponential back-off and starts again from 1 s after a successful connection", () => {
    vi.useFakeTimers();
    const c = client();
    c.socket.start();
    (FakeSocket.latest() as FakeSocket).serverClose(1006);
    expect(c.statuses.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(999);
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(2);
    (FakeSocket.latest() as FakeSocket).serverClose(1006);
    vi.advanceTimersByTime(1_999);
    expect(FakeSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(3);
    (FakeSocket.latest() as FakeSocket).ready();
    (FakeSocket.latest() as FakeSocket).serverClose(1001);
    vi.advanceTimersByTime(1_000);
    expect(FakeSocket.instances).toHaveLength(4);
    c.socket.stop();
  });

  it("stops for good when the server ends the session, with the server's code", () => {
    vi.useFakeTimers();
    const c = client();
    c.socket.start();
    const fake = FakeSocket.latest() as FakeSocket;
    fake.ready();
    fake.serverSend({ type: "session_ended", code: "SESSION_REVOKED", detail: "Signed out elsewhere." });
    fake.serverClose(4401);
    vi.advanceTimersByTime(60_000);
    expect(c.ended).toEqual(["SESSION_REVOKED"]);
    expect(c.statuses.at(-1)).toBe("closed");
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("treats a bare 4403 close as an inactive organization and 4401 as an expired session", () => {
    const forbidden = client();
    forbidden.socket.start();
    (FakeSocket.latest() as FakeSocket).serverClose(4403);
    expect(forbidden.ended).toEqual(["ORGANIZATION_INACTIVE"]);
    const expired = client();
    expired.socket.start();
    (FakeSocket.latest() as FakeSocket).serverClose(4401);
    expect(expired.ended).toEqual(["SESSION_EXPIRED"]);
  });

  it("never connects without a token, and uses wss behind https", () => {
    client(null).socket.start();
    expect(FakeSocket.instances).toHaveLength(0);
    expect(realtimeUrl({ protocol: "https:", host: "ops.example.test" })).toBe("wss://ops.example.test/api/v1/realtime/ws");
    expect(realtimeUrl({ protocol: "http:", host: "localhost:5173" })).toBe("ws://localhost:5173/api/v1/realtime/ws");
  });
});

// --- Connection indicator, polling fallback and session endings --------------------------------------------------------

describe("live status and session endings", () => {
  it("shows Connecting, Live, Reconnecting and Polling honestly", async () => {
    mockApi([[MINE, []]]);
    const signOut = vi.fn();
    shell(<p>page</p>, { signOut });
    expect((await screen.findByTestId("live-status")).textContent).toContain("Connecting…");
    const socket = await live();
    act(() => socket.serverClose(1006));
    expect(screen.getByTestId("live-status").textContent).toContain("Reconnecting…");
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2), { timeout: 2500 });
    const second = FakeSocket.latest() as FakeSocket;
    act(() => second.ready());
    expect(await screen.findByText("Live")).toBeTruthy();
    act(() => second.serverSend({ type: "session_ended", code: "ORGANIZATION_INACTIVE", detail: "Suspended." }));
    expect(signOut).toHaveBeenCalledWith("ORGANIZATION_INACTIVE");
    expect(screen.getByTestId("live-status").textContent).toContain("Polling");
  });

  it("polls while the WebSocket is down and stops polling once it is live", async () => {
    vi.useFakeTimers();
    mockApi([[MINE, []]]);
    const reload = vi.fn();
    function Probe() {
      useLiveRefresh(reload, () => false, { fallbackMs: 1_000 });
      return null;
    }
    shell(<Probe />);
    act(() => void vi.advanceTimersByTime(3_000));
    expect(reload).toHaveBeenCalledTimes(3);
    act(() => (FakeSocket.latest() as FakeSocket).ready());
    act(() => void vi.advanceTimersByTime(5_000));
    expect(reload).toHaveBeenCalledTimes(3);
  });

  it("signs out with a notice when the organization is suspended (WebSocket session_ended)", async () => {
    sessionStorage.setItem("cctv.accessToken", TOKEN);
    mockApi([{ method: "GET", url: /\/auth\/me$/, body: sessionFor("ADMIN") }, [MINE, []]]);
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AuthProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthProvider>
      </MemoryRouter>,
    );
    const socket = await live();
    act(() => socket.serverSend({ type: "session_ended", code: "ORGANIZATION_INACTIVE", detail: "Suspended." }));
    expect((await screen.findByTestId("signout-notice")).textContent).toContain("Your organization is inactive or suspended");
    expect(sessionStorage.getItem("cctv.accessToken")).toBeNull();
  });

  it("signs out when any request answers 403 ORGANIZATION_INACTIVE", async () => {
    sessionStorage.setItem("cctv.accessToken", TOKEN);
    mockApi([
      { method: "GET", url: /\/auth\/me$/, body: sessionFor("SUPERVISOR") },
      { method: "GET", url: MINE, body: { detail: "The organization is suspended.", code: "ORGANIZATION_INACTIVE" }, status: 403 },
    ]);
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AuthProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthProvider>
      </MemoryRouter>,
    );
    expect((await screen.findByTestId("signout-notice")).textContent).toContain("inactive or suspended");
  });

  it("does not sign out on other 403s (e.g. CALL_END_NOT_ALLOWED)", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    setToken(TOKEN);
    mockApi([{ method: "POST", url: /\/calls\/c1\/end$/, body: { detail: "Only staff or the starter can end it.", code: "CALL_END_NOT_ALLOWED" }, status: 403 }]);
    await expect(api("/calls/c1/end", { method: "POST" })).rejects.toMatchObject({ status: 403, code: "CALL_END_NOT_ALLOWED" });
    expect(handler).not.toHaveBeenCalled();
    mockApi([{ method: "GET", url: /\/calls$/, body: { detail: "Suspended.", code: "ORGANIZATION_INACTIVE" }, status: 403 }]);
    await expect(api("/calls")).rejects.toMatchObject({ code: "ORGANIZATION_INACTIVE" });
    expect(handler).toHaveBeenCalledWith("ORGANIZATION_INACTIVE");
  });
});

// --- Live pages -------------------------------------------------------------------------------------------------------

describe("live alert and case pages", () => {
  it("re-reads the alert chat on alert.message for this alert only", async () => {
    const thread: AlertMessage[] = [];
    const calls = mockApi([...alertRoutes(alert(), thread), { method: "GET", url: /\/alerts\/al1\/calls\/active$/, body: NO_CALL }, [MINE, []]]);
    shell(<AlertDetailPage />, { path: "/alerts/al1", route: "/alerts/:id" });
    const socket = await live();
    expect(await screen.findByText("No messages yet.")).toBeTruthy();
    const reads = () => sent(calls, "GET", /\/alerts\/al1\/messages$/).length;
    const before = reads();
    thread.push({ id: "m1", sender_role: "GUARD", sender_name: "Bilal Guard", body: "At the gate now", created_at: "2026-11-19T10:06:00Z" });
    act(() => socket.event("alert.message", { alert_id: "another-alert" }));
    await pause(400);
    expect(reads()).toBe(before);
    act(() => socket.event("alert.message", { alert_id: "al1" }));
    expect(await screen.findByText("At the gate now")).toBeTruthy();
    expect(reads()).toBe(before + 1);
  });

  it("shows Guard acknowledgements and responses as they happen (alert.updated)", async () => {
    const body = alert();
    mockApi([...alertRoutes(body), { method: "GET", url: /\/alerts\/al1\/calls\/active$/, body: NO_CALL }, [MINE, []]]);
    shell(<AlertDetailPage />, { role: "ADMIN", path: "/alerts/al1", route: "/alerts/:id" });
    const socket = await live();
    expect(await screen.findByText(/1 received · 0 acknowledged · 0 responding/)).toBeTruthy();
    Object.assign(body, {
      status: "ACKNOWLEDGED",
      acknowledged_count: 1,
      responding_count: 1,
      recipients: [{ ...body.recipients[0], state: "RESPONDING", assessment: "UNDER_CONTROL" }],
    });
    act(() => socket.event("alert.updated", { alert_id: "al1", state: "ACKNOWLEDGED" }));
    expect(await screen.findByText(/1 received · 1 acknowledged · 1 responding/)).toBeTruthy();
  });

  it("re-reads the case chat on incident.message and the case on incident.updated", async () => {
    const detail = incidentDetail();
    const thread: IncidentMessage[] = [];
    const calls = mockApi([
      { method: "GET", url: /\/incidents\/i1$/, body: detail },
      { method: "GET", url: /\/incidents\/i1\/messages$/, body: thread },
      { method: "GET", url: /\/incidents\/i1\/calls\/active$/, body: { ...NO_CALL } },
      [MINE, []],
    ]);
    shell(<IncidentDetailPage />, { path: "/incidents/i1", route: "/incidents/:id" });
    const socket = await live();
    await screen.findAllByText(/Weapon at the lobby/);
    thread.push({ id: "m1", sender_user_id: "g9", sender_name: "Roof Guard", sender_role: "GUARD", body: "Roof is clear", created_at: "2026-11-19T10:07:00Z" });
    act(() => socket.event("incident.message", { incident_id: "i1" }));
    expect(await screen.findByText("Roof is clear")).toBeTruthy();
    const before = sent(calls, "GET", /\/incidents\/i1$/).length;
    act(() => socket.event("incident.updated", { incident_id: "i1", kind: "NOTE_ADDED" }));
    await waitFor(() => expect(sent(calls, "GET", /\/incidents\/i1$/).length).toBe(before + 1));
  });

  it("updates the topbar bell at once on a notification signal and catches up after a reconnection", async () => {
    const unread: unknown[] = [];
    mockApi([[/\/notifications\?/, unread], [MINE, []]]);
    shell(<NotificationBell />);
    const socket = await live();
    expect(screen.getByRole("link", { name: "Notifications" })).toBeTruthy();
    unread.push({ id: "n1" });
    act(() => socket.event("notification", { kind: "ALERT_ASSIGNED" }));
    expect(await screen.findByRole("link", { name: "Notifications, 1 unread" })).toBeTruthy();

    // Signals sent while the socket was down are lost: the next "ready" makes everyone re-read once.
    unread.push({ id: "n2" });
    act(() => socket.serverClose(1006));
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2), { timeout: 2500 });
    act(() => (FakeSocket.latest() as FakeSocket).ready());
    expect(await screen.findByRole("link", { name: "Notifications, 2 unread" })).toBeTruthy();
  });
});

// --- Voice calls ------------------------------------------------------------------------------------------------------

describe("incoming calls", () => {
  it("rings at once on call.invited with the caller, role and alert, and declines", async () => {
    const mine: CallSession[] = [];
    const ringer = new FakeRinger();
    const calls = mockApi([{ method: "POST", url: /\/calls\/c1\/decline$/, body: callSession({ my_state: "DECLINED" }) }, [MINE, mine]]);
    shell(<p>page</p>, { ringer });
    const socket = await live();
    expect(screen.queryByTestId("incoming-call")).toBeNull();
    mine.push(callSession());
    act(() => socket.event("call.invited", { call_id: "c1", alert_id: "al1" }));
    const banner = await screen.findByTestId("incoming-call");
    expect(banner.textContent).toContain("Incoming alert call from Bilal Guard");
    expect(within(banner).getByText("Guard").className).toContain("role-guard");
    expect(within(banner).getByRole("link", { name: "Weapon alert · Gate Camera" }).getAttribute("href")).toBe("/alerts/al1");
    expect(banner.textContent).toContain("Admin Block → Ground → Main Gate");
    await waitFor(() => expect(ringer.ringing).toBe(true));

    mine.length = 0;
    fireEvent.click(screen.getByTestId("incoming-decline"));
    await waitFor(() => expect(screen.queryByTestId("incoming-call")).toBeNull());
    expect(sent(calls, "POST", /\/calls\/c1\/decline$/)).toHaveLength(1);
    expect(ringer.ringing).toBe(false);
  });

  it("says when the browser blocks the ringtone", async () => {
    mockApi([[MINE, [callSession()]]]);
    shell(<p>page</p>, { ringer: new FakeRinger(false) });
    expect(await screen.findByText(/Ringing is silent until you click/)).toBeTruthy();
  });

  it("finds an invitation by polling GET /calls while the WebSocket is down", async () => {
    mockApi([[MINE, [callSession({ scope: "INCIDENT", alert_id: null, incident_id: "i1", target_title: "INC-000007 · Weapon at the lobby", target_location: null })]]]);
    shell(<p>page</p>);
    const banner = await screen.findByTestId("incoming-call");
    expect(screen.getByTestId("live-status").textContent).not.toContain("Live");
    expect(banner.textContent).toContain("Incoming case call from Bilal Guard");
    expect(within(banner).getByRole("link", { name: "INC-000007 · Weapon at the lobby" }).getAttribute("href")).toBe("/incidents/i1");
  });
});

describe("the call dock", () => {
  function joinRoutes(join: CallJoin, mine: CallSession[], current: CallSession = join.call) {
    return [
      { method: "POST", url: /\/calls\/c1\/join$/, body: join },
      { method: "POST", url: /\/calls\/c1\/leave$/, body: { call: { ...current, my_state: "LEFT" }, call_ended: false } },
      { method: "POST", url: /\/calls\/c1\/end$/, body: { ...current, status: "ENDED", end_reason: "ENDED_BY_USER" } },
      { method: "GET", url: /\/calls\/c1$/, body: current },
      [MINE, mine] as [RegExp, unknown],
    ];
  }

  it("joins from the banner, shows who is talking, mutes, deafens and leaves", async () => {
    const voice = new FakeVoice();
    const join = joinedCall();
    const mine = [callSession()];
    const calls = mockApi(joinRoutes(join, mine));
    shell(<p>page</p>, { voice });
    mine.splice(0, 1, join.call);
    fireEvent.click(await screen.findByTestId("incoming-join"));
    const dock = await screen.findByTestId("call-dock");
    expect(voice.connected).toEqual({ url: "wss://voice.example.test", token: "lk-token" });
    expect(screen.queryByTestId("incoming-call")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("call-timer").textContent).toMatch(/^00:0\d · 1 connected$/));
    const people = within(dock).getByRole("list", { name: "Participants" });
    expect(within(people).getByText("Bilal Guard")).toBeTruthy();
    expect(within(people).getByText("Supervisor S (you)")).toBeTruthy();
    expect(within(people).getByText("Supervisor").className).toContain("role-supervisor");

    act(() => voice.events?.onParticipants(["g1"]));
    act(() => voice.events?.onSpeakers(["g1"]));
    expect(within(people).getByText("Speaking")).toBeTruthy();
    expect(screen.getByTestId("call-timer").textContent).toContain("2 connected");

    fireEvent.click(screen.getByTestId("call-mute"));
    await waitFor(() => expect(screen.getByTestId("call-mute").getAttribute("aria-pressed")).toBe("true"));
    expect(voice.microphone).toBe(false);
    fireEvent.click(screen.getByTestId("call-deafen"));
    expect(screen.getByTestId("call-deafen").getAttribute("aria-pressed")).toBe("true");
    expect(voice.deafened).toBe(true);

    mine.length = 0;
    fireEvent.click(screen.getByTestId("call-leave"));
    await waitFor(() => expect(screen.queryByTestId("call-dock")).toBeNull());
    expect(sent(calls, "POST", /\/calls\/c1\/leave$/)).toHaveLength(1);
    expect(voice.connected).toBeNull();
    expect(screen.queryByTestId("incoming-call")).toBeNull();
  });

  it("starts a call from the alert page (no organization or campus IDs sent) and ends it for everyone", async () => {
    const voice = new FakeVoice();
    const started: CallJoin = { ...joinedCall({ started_by: "X", started_by_name: "Supervisor S", started_by_role: "SUPERVISOR" }), outcome: "STARTED" };
    const mine: CallSession[] = [];
    const calls = mockApi([
      ...alertRoutes(alert()),
      { method: "GET", url: /\/alerts\/al1\/calls\/active$/, body: NO_CALL },
      { method: "POST", url: /\/alerts\/al1\/calls$/, body: started, status: 201 },
      ...joinRoutes(started, mine),
    ]);
    shell(<AlertDetailPage />, { path: "/alerts/al1", route: "/alerts/:id", voice });
    await live();
    const card = await screen.findByTestId("call-controls");
    expect(card.textContent).toContain("Rings this campus's Admins and Supervisors and the alert's Guards");
    fireEvent.click(within(card).getByTestId("call-start"));
    await screen.findByTestId("call-dock");
    const start = sent(calls, "POST", /\/alerts\/al1\/calls$/);
    expect(start).toHaveLength(1);
    expect(start[0].body).toBeUndefined();
    expect(start[0].url).not.toMatch(/organization|campus/);
    expect(voice.connected?.token).toBe("lk-token");

    fireEvent.click(await screen.findByTestId("call-end-all"));
    expect(await screen.findByText("You ended the call for everyone.")).toBeTruthy();
    expect(sent(calls, "POST", /\/calls\/c1\/end$/)).toHaveLength(1);
    expect(screen.queryByTestId("call-dock")).toBeNull();
    expect(voice.connected).toBeNull();
  });

  it("hides End for everyone when the server says this person cannot end the call", async () => {
    const join = joinedCall({ can_end: false });
    mockApi(joinRoutes(join, [callSession({ can_end: false })]));
    shell(<p>page</p>);
    fireEvent.click(await screen.findByTestId("incoming-join"));
    await screen.findByTestId("call-dock");
    expect(screen.getByTestId("call-leave")).toBeTruthy();
    expect(screen.queryByTestId("call-end-all")).toBeNull();
  });

  it("hangs up with a reason when call.updated says someone else ended the call", async () => {
    const voice = new FakeVoice();
    const join = joinedCall();
    const current = { ...join.call };
    mockApi(joinRoutes(join, [callSession()], current));
    shell(<p>page</p>, { voice });
    const socket = await live();
    fireEvent.click(await screen.findByTestId("incoming-join"));
    await screen.findByTestId("call-dock");
    Object.assign(current, { status: "ENDED", end_reason: "ENDED_BY_USER", ended_at: "2026-11-19T10:09:00Z" });
    act(() => socket.event("call.updated", { call_id: "c1", alert_id: "al1", state: "ENDED" }));
    expect(await screen.findByText("The call was ended for everyone.")).toBeTruthy();
    expect(screen.queryByTestId("call-dock")).toBeNull();
    expect(voice.connected).toBeNull();
  });

  it("does not leave the call on the server when the same account joined from another tab or device", async () => {
    const voice = new FakeVoice();
    const join = joinedCall();
    const calls = mockApi(joinRoutes(join, [callSession()]));
    shell(<p>page</p>, { voice });
    fireEvent.click(await screen.findByTestId("incoming-join"));
    await screen.findByTestId("call-dock");
    act(() => voice.events?.onDisconnected("elsewhere"));
    expect(await screen.findByText("You joined this call from another tab or device, so it continues there.")).toBeTruthy();
    expect(screen.queryByTestId("call-dock")).toBeNull();
    expect(sent(calls, "POST", /\/calls\/c1\/leave$/)).toHaveLength(0);
  });

  it("leaves the call on the server when the audio connection is lost", async () => {
    const voice = new FakeVoice();
    const join = joinedCall();
    const calls = mockApi(joinRoutes(join, [callSession()]));
    shell(<p>page</p>, { voice });
    fireEvent.click(await screen.findByTestId("incoming-join"));
    await screen.findByTestId("call-dock");
    act(() => voice.events?.onDisconnected("lost"));
    expect(await screen.findByText("The call audio disconnected.")).toBeTruthy();
    await waitFor(() => expect(sent(calls, "POST", /\/calls\/c1\/leave$/)).toHaveLength(1));
  });

  it("leaves the call before signing out, while the token still works", async () => {
    let hook: (() => void) | null = null;
    const beforeSignOut = (h: () => void) => {
      hook = h;
      return () => {
        hook = null;
      };
    };
    const voice = new FakeVoice();
    const join = joinedCall();
    const calls = mockApi(joinRoutes(join, [callSession()]));
    shell(<p>page</p>, { voice, beforeSignOut });
    fireEvent.click(await screen.findByTestId("incoming-join"));
    await screen.findByTestId("call-dock");
    act(() => (hook as unknown as () => void)());
    await waitFor(() => expect(sent(calls, "POST", /\/calls\/c1\/leave$/)).toHaveLength(1));
    expect(voice.connected).toBeNull();
  });

  it("joins an active call from the case page's Join active call button", async () => {
    const join = joinedCall({ scope: "INCIDENT", alert_id: null, incident_id: "i1" });
    const ongoing = callSession({ scope: "INCIDENT", alert_id: null, incident_id: "i1", my_state: null });
    const calls = mockApi([
      { method: "GET", url: /\/incidents\/i1$/, body: incidentDetail() },
      { method: "GET", url: /\/incidents\/i1\/messages$/, body: [] },
      { method: "GET", url: /\/incidents\/i1\/calls\/active$/, body: { ...NO_CALL, call: ongoing } },
      ...joinRoutes(join, []),
    ]);
    shell(<IncidentDetailPage />, { role: "ADMIN", path: "/incidents/i1", route: "/incidents/:id" });
    expect((await screen.findByTestId("active-call-banner")).textContent).toContain("1 in the call (Bilal Guard, Guard)");
    fireEvent.click(screen.getByTestId("call-join"));
    await screen.findByTestId("call-dock");
    expect(sent(calls, "POST", /\/calls\/c1\/join$/)).toHaveLength(1);
  });
});

describe("honest voice notices", () => {
  it("says when the server has no voice provider (VOICE_CALL_PROVIDER=none) and keeps Start disabled", async () => {
    mockApi([
      ...alertRoutes(alert()),
      { method: "GET", url: /\/alerts\/al1\/calls\/active$/, body: { voice_calls_available: false, provider: "none", call: null } },
      [MINE, []],
    ]);
    shell(<AlertDetailPage />, { path: "/alerts/al1", route: "/alerts/:id" });
    expect((await screen.findByTestId("voice-not-configured")).textContent).toContain("VOICE_CALL_PROVIDER=none");
    expect((screen.getByTestId("call-start") as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText("No messages yet.")).toBeTruthy();
  });

  it("explains 503 VOICE_CALL_NOT_CONFIGURED from the start route", async () => {
    const voice = new FakeVoice();
    mockApi([
      ...alertRoutes(alert()),
      { method: "GET", url: /\/alerts\/al1\/calls\/active$/, body: NO_CALL },
      { method: "POST", url: /\/alerts\/al1\/calls$/, body: { detail: "Voice calls are not configured.", code: "VOICE_CALL_NOT_CONFIGURED" }, status: 503 },
      [MINE, []],
    ]);
    shell(<AlertDetailPage />, { path: "/alerts/al1", route: "/alerts/:id", voice });
    fireEvent.click(await screen.findByTestId("call-start"));
    expect((await screen.findByTestId("call-notice")).textContent).toContain(NOT_CONFIGURED_NOTICE);
    expect(voice.connected).toBeNull();
  });

  it("keeps the call when the microphone is denied: listening only, muted, with the reason", async () => {
    const denied = microphoneMessage(new DOMException("Permission denied", "NotAllowedError"));
    expect(denied).toBe("Microphone permission was denied. Allow the microphone for this site to talk; you can still listen.");
    const voice = new FakeVoice(true, null, { microphone: false, microphoneError: denied });
    mockApi([
      { method: "POST", url: /\/calls\/c1\/join$/, body: joinedCall() },
      { method: "GET", url: /\/calls\/c1$/, body: joinedCall().call },
      [MINE, [callSession()]],
    ]);
    shell(<p>page</p>, { voice });
    fireEvent.click(await screen.findByTestId("incoming-join"));
    const dock = await screen.findByTestId("call-dock");
    expect(within(dock).getByText(denied)).toBeTruthy();
    expect(screen.getByTestId("call-mute").getAttribute("aria-pressed")).toBe("true");
  });

  it("says why a browser cannot make calls and disables joining", async () => {
    const reason = browserVoiceSupport({ secure: true, hasPeerConnection: false, hasMediaDevices: true }) as string;
    expect(reason).toBe("This browser cannot make voice calls (no WebRTC support).");
    expect(browserVoiceSupport({ secure: false, hasPeerConnection: true, hasMediaDevices: true })).toMatch(/secure \(https\)/);
    expect(browserVoiceSupport({ secure: true, hasPeerConnection: true, hasMediaDevices: true })).toBeNull();
    mockApi([[MINE, [callSession()]]]);
    shell(<p>page</p>, { voice: new FakeVoice(false, reason) });
    const banner = await screen.findByTestId("incoming-call");
    expect(within(banner).getByText(reason)).toBeTruthy();
    expect((screen.getByTestId("incoming-join") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows call controls to both Admins and Supervisors", async () => {
    for (const role of ["ADMIN", "SUPERVISOR"] as const) {
      mockApi([...alertRoutes(alert()), { method: "GET", url: /\/alerts\/al1\/calls\/active$/, body: NO_CALL }, [MINE, []]]);
      const view = shell(<AlertDetailPage />, { role, path: "/alerts/al1", route: "/alerts/:id" });
      expect(await screen.findByTestId("call-start")).toBeTruthy();
      view.unmount();
    }
  });
});
