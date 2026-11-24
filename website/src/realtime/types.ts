/** The real-time WebSocket contract (decision D-025/D-026; identical to the Guard app's mobile/src/api/types.ts). */

export type RealtimeEventName =
  | "alert.assigned"
  | "alert.updated"
  | "alert.message"
  | "incident.message"
  | "incident.updated"
  | "incident.invited"
  | "incident.removed"
  | "notification"
  | "call.invited"
  | "call.updated";

export interface RealtimeEvent {
  type: "event";
  event: RealtimeEventName;
  at: string;
  alert_id?: string;
  incident_id?: string;
  call_id?: string;
  /** Alert, case or call status, when the change is one. */
  state?: string;
  /** Notification kind, or the case history kind (NOTE_ADDED, STATUS_CHANGED, …). */
  kind?: string;
}

export type RealtimeServerMessage =
  | { type: "ready"; user_id: string; role: string; heartbeat_seconds: number; token_expires_at: string }
  | { type: "pong" }
  | RealtimeEvent
  | { type: "session_ended"; code: string; detail: string }
  | { type: "error"; code: string; detail: string };

export type SocketStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed";
