/**
 * The real-time WebSocket client (decision D-025). Protocol: connect → {"type": "auth", "access_token"} → "ready";
 * {"type": "ping"} every heartbeat → "pong"; events {"type": "event", ...}; {"type": "session_ended", code} followed by
 * close 4401 / 4403 when the session is no longer valid. Reconnects with back-off after unexpected closes.
 */
import type { RealtimeServerMessage } from "../api/types";

export type SocketStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed";

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
  onEvent: (event: Extract<RealtimeServerMessage, { type: "event" }>) => void;
  /** The server ended the session (ASSIGNMENT_CHANGED, ORGANIZATION_INACTIVE, SESSION_EXPIRED, …). */
  onSessionEnded: (code: string, detail: string) => void;
  /** Called after every (re)connection, so the app can resync what it missed. */
  onReady?: () => void;
}

export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_FORBIDDEN = 4403;
const OPEN = 1;

export class RealtimeSocket {
  private socket: SocketLike | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private attempts = 0;
  private stopped = true;
  private sessionEnded = false;

  constructor(
    private readonly url: string,
    private readonly getToken: () => string | null,
    private readonly handlers: RealtimeHandlers,
    private readonly factory: SocketFactory = (u) => new WebSocket(u) as unknown as SocketLike,
    private readonly heartbeatMs = 25_000,
  ) {}

  start(): void {
    this.stopped = false;
    this.sessionEnded = false;
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
    this.handlers.onStatus("closed");
  }

  /** Reconnect now (e.g. the network came back), skipping the back-off. */
  reconnectNow(): void {
    if (this.stopped || this.sessionEnded) return;
    if (this.socket && this.socket.readyState === OPEN) return;
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
    socket.onerror = () => undefined; // followed by onclose
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.heartbeat) clearInterval(this.heartbeat);
      this.heartbeat = null;
      if (this.stopped || this.sessionEnded) return;
      if (event.code === CLOSE_UNAUTHORIZED || event.code === CLOSE_FORBIDDEN) {
        // Refused without a session_ended message: treat as an ended session.
        this.sessionEnded = true;
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
    switch (message.type) {
      case "ready":
        this.attempts = 0;
        this.handlers.onStatus("open");
        if (this.heartbeat) clearInterval(this.heartbeat);
        this.heartbeat = setInterval(() => {
          if (this.socket && this.socket.readyState === OPEN) this.socket.send(JSON.stringify({ type: "ping" }));
        }, Math.min(this.heartbeatMs, message.heartbeat_seconds * 1000));
        this.handlers.onReady?.();
        break;
      case "event":
        this.handlers.onEvent(message);
        break;
      case "session_ended":
        this.sessionEnded = true;
        this.clearTimers();
        this.handlers.onSessionEnded(message.code, message.detail);
        break;
      default:
        break;
    }
  }
}
