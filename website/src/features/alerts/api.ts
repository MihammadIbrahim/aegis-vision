import { api } from "../../api/client";
import type { Alert, AlertEscalation, AlertEvent, AlertMessage, AlertQuery, EscalationBody } from "./types";

/** One ID per user action: if the same request is sent again, the server applies it only once. */
function actionId(): string {
  return crypto.randomUUID();
}

export const alerts = {
  list: (query: AlertQuery) => api<Alert[]>("/alerts", { query: { ...query } }),
  get: (id: string) => api<Alert>(`/alerts/${id}`),
  history: (id: string) => api<AlertEvent[]>(`/alerts/${id}/history`),
  resolve: (id: string, note: string | null) =>
    api<Alert>(`/alerts/${id}/resolve`, { json: { note, client_action_id: actionId() } }),
  dismiss: (id: string, reason: string) =>
    api<Alert>(`/alerts/${id}/dismiss`, { json: { reason, client_action_id: actionId() } }),
  escalate: (id: string, body: EscalationBody) =>
    api<{ alert: Alert; added_guards: number }>(`/alerts/${id}/escalate`, {
      json: { ...body, client_action_id: actionId() },
    }),
  escalations: (id: string) => api<AlertEscalation[]>(`/alerts/${id}/escalations`),
  /** Sets (or clears, with null) the Admin or Supervisor responsible for the alert. */
  setHandler: (id: string, user_id: string | null) => api<Alert>(`/alerts/${id}/handler`, { method: "PUT", json: { user_id } }),
  /** Paths for <AuthMedia>: the detection snapshot and the recording around the detection. */
  paths: {
    snapshot: (id: string) => `/alerts/${id}/snapshot`,
    clip: (id: string) => `/alerts/${id}/clip`,
  },
  messages: (id: string) => api<AlertMessage[]>(`/alerts/${id}/messages`),
  post: (id: string, body: string) =>
    api<AlertMessage[]>(`/alerts/${id}/messages`, { json: { body, client_message_id: crypto.randomUUID() } }),
};
