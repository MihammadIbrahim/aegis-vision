import { api } from "../../api/client";
import type { ActiveCall, CallJoin, CallSession, CallTarget, LeaveResult } from "./types";

function targetPath(target: CallTarget): string {
  return target.scope === "ALERT" ? `/alerts/${target.id}` : `/incidents/${target.id}`;
}

/** Every voice-call route the web app uses (all under /api/v1). */
export const callsApi = {
  active: (target: CallTarget) => api<ActiveCall>(`${targetPath(target)}/calls/active`),
  start: (target: CallTarget) => api<CallJoin>(`${targetPath(target)}/calls`, { method: "POST" }),
  mine: () => api<CallSession[]>("/calls"),
  get: (callId: string) => api<CallSession>(`/calls/${callId}`),
  join: (callId: string) => api<CallJoin>(`/calls/${callId}/join`, { method: "POST" }),
  decline: (callId: string) => api<CallSession>(`/calls/${callId}/decline`, { method: "POST" }),
  leave: (callId: string) => api<LeaveResult>(`/calls/${callId}/leave`, { method: "POST" }),
  end: (callId: string) => api<CallSession>(`/calls/${callId}/end`, { method: "POST" }),
};

export const ROLE_LABEL: Record<string, string> = { ADMIN: "Admin", SUPERVISOR: "Supervisor", GUARD: "Guard" };

export function targetLink(call: Pick<CallSession, "scope" | "alert_id" | "incident_id">): string {
  return call.scope === "ALERT" ? `/alerts/${call.alert_id}` : `/incidents/${call.incident_id}`;
}
