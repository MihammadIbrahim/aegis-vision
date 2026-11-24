/* Test helpers: a fetch mock with a route table, fakes for the WebSocket, alarm engine and voice client, and the
   provider stack of the signed-in app. */
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";

import type { AlarmEngine } from "../alarm/engine";
import { AlarmProvider } from "../alarm/AlarmProvider";
import type { AlertResponse, GuardDashboardResponse } from "../api/types";
import { AuthFlowProvider } from "../auth/AuthFlowProvider";
import { SessionProvider, useSession } from "../auth/SessionProvider";
import { CallProvider } from "../calls/CallProvider";
import type { VoiceClient, VoiceEvents } from "../calls/voiceClient";
import { API_BASE_URL, API_PREFIX } from "../config";
import { NetworkProvider } from "../lib/network";
import { createQueryClient } from "../lib/queryClient";
import { SECURE_KEYS } from "../lib/storageKeys";
import { PushStatusProvider } from "../notifications/PushStatus";
import type { OutboxItem, OutboxStore } from "../offline/outbox";
import { OutboxProvider } from "../offline/OutboxProvider";
import { RealtimeProvider } from "../realtime/RealtimeProvider";
import type { SocketLike } from "../realtime/socket";

// --- fetch ------------------------------------------------------------------------------------------------------------

export interface RecordedCall {
  method: string;
  path: string;
  body: unknown;
  headers: Record<string, string>;
}

export type Reply = { status?: number; body?: unknown } | "NETWORK";
export type Handler = (call: RecordedCall) => Reply;

export const NETWORK: Reply = "NETWORK";

export function ok(body: unknown, status = 200): Reply {
  return { status, body };
}

export function fail(status: number, detail: string, code?: string, extra: Record<string, unknown> = {}): Reply {
  return { status, body: { detail, ...(code ? { code } : {}), ...extra } };
}

/** Replaces global.fetch. Routes are "METHOD /path" (query string ignored unless the route includes it). */
export function mockApi(routes: Record<string, Handler | Reply>) {
  const calls: RecordedCall[] = [];
  const fetchMock = jest.fn(async (url: string, init: { method?: string; body?: string; headers?: Record<string, string> } = {}) => {
    const full = String(url).replace(`${API_BASE_URL}${API_PREFIX}`, "");
    const method = (init.method ?? "GET").toUpperCase();
    const call: RecordedCall = {
      method,
      path: full,
      body: init.body ? JSON.parse(init.body) : undefined,
      headers: init.headers ?? {},
    };
    calls.push(call);
    const route = routes[`${method} ${full}`] ?? routes[`${method} ${full.split("?")[0]}`];
    const reply: Reply = route === undefined ? { status: 404, body: { detail: `Not mocked: ${method} ${full}` } } : typeof route === "function" ? route(call) : route;
    if (reply === "NETWORK") throw new TypeError("Network request failed");
    const status = reply.status ?? 200;
    const text = reply.body === undefined ? "" : JSON.stringify(reply.body);
    return { ok: status >= 200 && status < 300, status, text: async () => text };
  });
  (globalThis as unknown as { fetch: unknown }).fetch = fetchMock;
  return {
    calls,
    fetchMock,
    find: (method: string, path: string) => calls.filter((c) => c.method === method && c.path.split("?")[0] === path),
  };
}

// --- Session -----------------------------------------------------------------------------------------------------------

 
export function secureStore(): Map<string, string> {
  return require("expo-secure-store").__store as Map<string, string>;
}

export function setOnline(online: boolean): void {
  require("@react-native-community/netinfo").__setOnline(online);
}
 

export function storeSession(mode: "STANDBY" | "STATIONED" = "STATIONED", token = "test-token"): void {
  const store = secureStore();
  store.set(SECURE_KEYS.accessToken, JSON.stringify(token));
  store.set(
    SECURE_KEYS.session,
    JSON.stringify({
      operational_mode: mode,
      dashboard: mode === "STANDBY" ? "STANDBY_EMERGENCY" : "STATIONED_MONITORING",
      expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
    }),
  );
}

// --- Fakes -------------------------------------------------------------------------------------------------------------

export class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number; reason?: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  static latest(): FakeSocket {
    return FakeSocket.instances[FakeSocket.instances.length - 1];
  }
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }
  serverSend(message: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  serverClose(code: number): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }
  close(): void {
    this.readyState = 3;
  }
}

export const fakeSocketFactory = (url: string) => new FakeSocket(url);

export class FakeEngine implements AlarmEngine {
  ringing = false;
  starts = 0;
  stops = 0;
  async start(): Promise<void> {
    if (!this.ringing) this.starts += 1;
    this.ringing = true;
  }
  async stop(): Promise<void> {
    if (this.ringing) this.stops += 1;
    this.ringing = false;
  }
}

export class FakeVoice implements VoiceClient {
  connected: { url: string; token: string } | null = null;
  events: VoiceEvents | null = null;
  microphone = true;
  speaker = false;
  constructor(
    readonly available = true,
    readonly unavailableReason: string | null = null,
  ) {}
  async connect(url: string, token: string, events: VoiceEvents): Promise<void> {
    this.connected = { url, token };
    this.events = events;
  }
  async disconnect(): Promise<void> {
    this.connected = null;
  }
  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    this.microphone = enabled;
  }
  async setSpeakerphone(on: boolean): Promise<void> {
    this.speaker = on;
  }
}

export function memoryOutboxStore(initial: OutboxItem[] = []): OutboxStore & { items: OutboxItem[]; photos: Map<string, string> } {
  const photos = new Map<string, string>();
  const store = {
    items: initial,
    photos,
    load: async () => store.items,
    save: async (items: OutboxItem[]) => {
      store.items = items;
    },
    writePhoto: async (id: string, base64: string) => {
      photos.set(`mem://${id}`, base64);
      return `mem://${id}`;
    },
    readPhoto: async (uri: string) => photos.get(uri) ?? "",
    deletePhoto: async (uri: string) => {
      photos.delete(uri);
    },
  };
  return store;
}

// --- Data --------------------------------------------------------------------------------------------------------------

export function dashboard(mode: "STANDBY" | "STATIONED" = "STATIONED"): GuardDashboardResponse {
  return {
    organization: { organization_id: "UCPABC12345678901", name: "UCP" },
    campus: { name: "Main Campus" },
    guard: {
      guard_id: "ABCDEF123456789012345678901",
      full_name: "Gul A",
      username: "gul.a",
      email: "ga@ucp.example.com",
      phone_number: "+923001234567",
      role: "GUARD",
    },
    not_available_yet: [],
    operational_mode: mode,
    dashboard: mode === "STANDBY" ? "STANDBY_EMERGENCY" : "STATIONED_MONITORING",
  };
}

export function alert(overrides: Partial<AlertResponse> & { myState?: "NOTIFIED" | "ACKNOWLEDGED" | "RESPONDING"; reason?: "LOCATION" | "ESCALATION" | "AUTOMATIC" | "ASSISTANCE" } = {}): AlertResponse {
  const { myState = "NOTIFIED", reason = "LOCATION", ...rest } = overrides;
  const now = new Date().toISOString();
  return {
    id: "11111111-2222-4333-8444-555555555555",
    alert_type: "WEAPON",
    priority: "HIGH",
    status: "NEW",
    camera_id: "99999999-2222-4333-8444-555555555555",
    camera_name: "Lobby Cam",
    location: {
      building_id: "b1",
      building_name: "Admin Block",
      building_kind: "INDOOR",
      floor_id: "f1",
      floor_name: "Ground",
      area_id: "a1",
      area_name: "Lobby",
    },
    confidence: "0.9100",
    detected_at: now,
    last_detected_at: now,
    detection_count: 1,
    close_note: null,
    closed_at: null,
    created_at: now,
    updated_at: now,
    recipients: [
      {
        guard_id: "ABCDEF123456789012345678901",
        full_name: "Gul A",
        reason,
        state: myState,
        assessment: null,
        acknowledged_at: myState === "NOTIFIED" ? null : now,
        responding_at: myState === "RESPONDING" ? now : null,
      },
    ],
    handled_by: null,
    handled_by_name: null,
    handled_at: null,
    recipient_count: 1,
    acknowledged_count: 0,
    responding_count: 0,
    has_snapshot: false,
    ...rest,
  };
}

/** The routes every signed-in screen touches, plus the given ones. */
export function signedInRoutes(extra: Record<string, Handler | Reply> = {}, mode: "STANDBY" | "STATIONED" = "STATIONED") {
  return {
    "GET /guard/dashboard": ok(dashboard(mode)),
    "GET /guard/location": ok(
      mode === "STATIONED"
        ? { assigned: true, building_name: "Admin Block", building_kind: "INDOOR", floor_name: "Ground", area_name: "Lobby" }
        : { assigned: false },
    ),
    "GET /alerts": ok([]),
    "GET /calls": ok([]),
    "GET /notifications": ok([]),
    ...extra,
  };
}

// --- Providers ---------------------------------------------------------------------------------------------------------

export interface ProviderOptions {
  /** Rendered beside the signed-in part (stays mounted after a sign-out). */
  outside?: ReactNode;
  engine?: AlarmEngine;
  voice?: VoiceClient;
  store?: OutboxStore;
  signedIn?: boolean;
}

/** Like the (app) layout: signed-in providers start only once the stored session is loaded. */
function SignedInGate({ children }: { children: ReactNode }) {
  const { state } = useSession();
  return state.status === "signedIn" ? <>{children}</> : null;
}

export function AppProviders({ children, engine, voice, store, signedIn = true, outside }: ProviderOptions & { children: ReactNode }) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false, staleTime: 0, gcTime: Infinity }, mutations: { retry: false, gcTime: Infinity } });
  const app = signedIn ? (
    <SignedInGate>
    <PushStatusProvider register={async () => ({ status: "unavailable", reason: "Test" })} forget={async () => undefined}>
      <OutboxProvider store={store ?? memoryOutboxStore()}>
        <AlarmProvider engine={engine ?? new FakeEngine()}>
          <CallProvider voice={voice ?? new FakeVoice()}>{children}</CallProvider>
        </AlarmProvider>
      </OutboxProvider>
    </PushStatusProvider>
    </SignedInGate>
  ) : (
    children
  );
  return (
    <QueryClientProvider client={client}>
      <NetworkProvider>
        <SessionProvider>
          <AuthFlowProvider>
            <RealtimeProvider socketFactory={fakeSocketFactory}>
              {outside}
              {app}
            </RealtimeProvider>
          </AuthFlowProvider>
        </SessionProvider>
      </NetworkProvider>
    </QueryClientProvider>
  );
}

export function withProviders(ui: ReactElement, options: ProviderOptions = {}): ReactElement {
  return <AppProviders {...options}>{ui}</AppProviders>;
}
