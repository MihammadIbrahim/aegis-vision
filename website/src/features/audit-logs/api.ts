import { api, buildUrl, download } from "../../api/client";

// Audit logs of the campus (Org Admin page 21, Supervisor page 17; decision D-024).

export interface AuditRecord {
  id: string;
  occurred_at: string;
  category: string;
  action: string;
  outcome: "SUCCESS" | "FAILURE" | "DENIED";
  actor_user_id: string | null;
  actor_display_id: string | null;
  actor_name: string | null;
  actor_role: string;
  target_type: string;
  target_id: string | null;
  target_label: string | null;
  details: Record<string, unknown>;
}

export interface AuditPage {
  items: AuditRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface AuditQuery {
  category?: string[];
  actor_role?: string;
  actor?: string;
  action?: string;
  outcome?: string;
  q?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export const CATEGORY_LABELS: Record<string, string> = {
  AUTHENTICATION: "Sign-in & passwords",
  USER_MANAGEMENT: "Staff & accounts",
  CAMERA_LOCATION: "Cameras & locations",
  AI_SETTINGS: "AI configuration",
  ALERTS: "Alert handling",
  GUARD_RESPONSE: "Guard responses",
  INCIDENT_CHAT: "Alert & case chat",
  INCIDENTS: "Incidents",
  EVIDENCE: "Evidence",
  RETENTION: "Retention & storage",
  VIDEO: "Live & recordings",
  PERSON_SEARCH: "Person search",
  MANUAL_REPORTS: "Manual reports",
  PERSONNEL: "Personnel & access",
  ORGANIZATION: "Organization & campuses",
  AI_ASSISTANT: "AI chatbot",
  OTHER: "Other",
};

export function humanAction(action: string): string {
  const text = action.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export const auditLogs = {
  page: (query: AuditQuery) => api<AuditPage>("/audit-logs", { query: { ...query } }),
  categories: () => api<{ categories: string[] }>("/audit-logs/categories"),
  exportCsv: (query: AuditQuery) =>
    download(buildUrl("/audit-logs/export", { ...query }).replace(/^\/api\/v1/, ""), "audit-logs.csv"),
};
