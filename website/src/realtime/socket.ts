/**
 * The real-time WebSocket client (decisions D-025, D-026), the same protocol as the Guard app:
 * connect → {"type": "auth", "access_token"} (never in the URL) → "ready"; {"type": "ping"} every heartbeat → "pong";
 * events {"type": "event", …} carry IDs only; {"type": "session_ended", code} then close 4401 / 4403 when the
 * session is no longer valid. Unexpected closes reconnect with exponential back-off (1 s … 30 s).
 */
import type { RealtimeEvent, RealtimeServerMessage, SocketStatus } from "./types";

export interface SocketLike {
  readyState: number;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason?: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export type SocketFactory = (url: string) => SocketLike;

export interface RealtimeHandlers {
  onStatus: (status: SocketStatus) => void;
  onEvent: (event: RealtimeEvent) => void;
  onSessionEnded: (code: string, detail: string) => void;
  /** After every (re)connection, so pages can catch up on what they missed. */
  onReady?: () => void;
}

export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_FORBIDDEN = 4403;
const OPEN = 1;

/** ws(s)://<this host>/api/v1/realtime/ws (the dev server proxies it to the Core API). */
export function realtimeUrl(location: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/realtime/ws`;
}

let defaultFactory: SocketFactory = (url) => new WebSocket(url) as unknown as SocketLike;

/** Tests install a fake here, so no page ever opens a real socket under jsdom. */
export function setRealtimeSocketFactory(factory: SocketFactory): void {
  defaultFactory = factory;
}

export class RealtimeSocket {
  private socket: SocketLike | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private attempts = 0;
  private stopped = true;
  private ended = false;

  constructor(
    private readonly url: string,
    private readonly getToken: () => string | null,
    private readonly handlers: RealtimeHandlers,
    private readonly factory: SocketFactory = defaultFactory,
  ) {}

  start(): void {
    this.stopped = false;
    this.ended = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = null;
      socket.onmessage = null;
      try {
        socket.close(1000, "client stop");
      } catch {
        // already closed
      }
    }
  }

  /** Reconnect at once (e.g. the browser came back online). */
  reconnectNow(): void {
    if (this.stopped || this.ended || (this.socket && this.socket.readyState === OPEN)) return;
    if (this.retry) clearTimeout(this.retry);
    this.retry = null;
    this.open();
  }

  private clearTimers(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    if (this.retry) clearTimeout(this.retry);
    this.heartbeat = null;
    this.retry = null;
  }

  private open(): void {
    const token = this.getToken();
    if (!token || this.stopped) return;
    this.handlers.onStatus(this.attempts === 0 ? "connecting" : "reconnecting");
    let socket: SocketLike;
    try {
      socket = this.factory(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => socket.send(JSON.stringify({ type: "auth", access_token: token }));
    socket.onmessage = (event) => this.handle(event.data);
    socket.onerror = () => undefined; // onclose follows
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.heartbeat) clearInterval(this.heartbeat);
      this.heartbeat = null;
      if (this.stopped || this.ended) return;
      if (event.code === CLOSE_UNAUTHORIZED || event.code === CLOSE_FORBIDDEN) {
        this.ended = true;
        this.handlers.onStatus("closed");
        this.handlers.onSessionEnded(event.code === CLOSE_FORBIDDEN ? "ORGANIZATION_INACTIVE" : "SESSION_EXPIRED", "");
        return;
      }
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    this.handlers.onStatus("reconnecting");
    const delay = Math.min(30_000, 1_000 * 2 ** Math.min(this.attempts, 5));
    this.attempts += 1;
    this.retry = setTimeout(() => {
      this.retry = null;
      this.open();
    }, delay);
  }

  private handle(data: unknown): void {
    let message: RealtimeServerMessage;
    try {
      message = JSON.parse(String(data)) as RealtimeServerMessage;
    } catch {
      return;
    }
    if (message.type === "ready") {
      this.attempts = 0;
      this.handlers.onStatus("open");
      if (this.heartbeat) clearInterval(this.heartbeat);
      this.heartbeat = setInterval(() => {
        if (this.socket && this.socket.readyState === OPEN) this.socket.send(JSON.stringify({ type: "ping" }));
      }, Math.max(5, message.heartbeat_seconds) * 1000);
      this.handlers.onReady?.();
    } else if (message.type === "event") {
      this.handlers.onEvent(message);
    } else if (message.type === "session_ended") {
      this.ended = true;
      this.clearTimers();
      this.handlers.onStatus("closed");
      this.handlers.onSessionEnded(message.code, message.detail);
    }
  }
}
