import { api } from "../../api/client";
import type { AssistantExchange, AssistantSession, AssistantSessionDetail, AssistantStatus } from "./types";

/** Every /api/v1/assistant route the web app uses (decision D-027). Read-only: nothing here changes a record. */
export const assistantApi = {
  status: () => api<AssistantStatus>("/assistant/status"),
  sessions: () => api<AssistantSession[]>("/assistant/sessions"),
  create: (body: { title?: string; context_incident_id?: string } = {}) =>
    api<AssistantSession>("/assistant/sessions", { json: body }),
  get: (id: string) => api<AssistantSessionDetail>(`/assistant/sessions/${id}`),
  remove: (id: string) => api<void>(`/assistant/sessions/${id}`, { method: "DELETE" }),
  send: (id: string, content: string) =>
    api<AssistantExchange>(`/assistant/sessions/${id}/messages`, { json: { content } }),
  investigate: (incidentId: string) =>
    api<AssistantExchange>(`/assistant/incidents/${incidentId}/investigate`, { method: "POST" }),
};

/** Starter prompts, one or two per capability area. */
export const STARTER_PROMPTS: string[] = [
  "How many incidents happened last year and which type was most frequent?",
  "What are our peak incident hours and top hotspot buildings?",
  "Which cameras went offline most often this month?",
  "What is our average Guard response and escalation rate?",
  "Did weapon alerts increase this month compared with last month?",
  "Summarize manual reports at the Main Gate over the last 3 months",
  "Show evidence tagged weapon from last month",
];

export const ENTITY_LABEL: Record<string, string> = {
  ALERT: "Alert",
  INCIDENT: "Case",
  CAMERA: "Camera",
  EVIDENCE: "Evidence",
  MANUAL_REPORT: "Manual report",
  LOCATION: "Place",
};

export function providerText(provider: string | null, model: string | null): string {
  if (provider === "ollama") return `Ollama · ${model ?? "local model"}`;
  if (provider === "openai_compatible") return `LLM · ${model ?? "model"}`;
  return "Local deterministic engine";
}
