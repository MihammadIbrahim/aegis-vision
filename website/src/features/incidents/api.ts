import { api } from "../../api/client";

// Incidents & Investigations (Org Admin page 6, Supervisor page 6; decision D-024).

export type IncidentStatus = "OPEN" | "UNDER_INVESTIGATION" | "RESPONDING" | "RESOLVED" | "DISMISSED";
export type IncidentPriority = "HIGH" | "STANDARD";

export const STATUS_ORDER: IncidentStatus[] = ["OPEN", "UNDER_INVESTIGATION", "RESPONDING", "RESOLVED", "DISMISSED"];
export const CLOSED: IncidentStatus[] = ["RESOLVED", "DISMISSED"];

export interface Incident {
  id: string;
  reference: string;
  title: string;
  description: string;
  status: IncidentStatus;
  priority: IncidentPriority;
  occurred_at: string;
  camera_id: string | null;
  camera_name: string | null;
  building_id: string | null;
  floor_id: string | null;
  area_id: string | null;
  location: string | null;
  assigned_to: string | null;
  assigned_name: string | null;
  created_by: string | null;
  created_by_name: string;
  resolution_note: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  alert_count: number;
  report_count: number;
  evidence_count: number;
}

export interface IncidentPage {
  items: Incident[];
  total: number;
  limit: number;
  offset: number;
  counts: Record<IncidentStatus, number>;
}

export interface LinkedAlert {
  alert_id: string;
  alert_type: string;
  priority: string;
  status: string;
  confidence: number | null;
  detected_at: string;
  last_detected_at: string;
  detection_count: number;
  camera_id: string | null;
  camera_name: string;
  location: string | null;
  has_snapshot: boolean;
  linked_by_name: string;
  linked_at: string;
}

export interface LinkedReport {
  report_id: string;
  report_type: string;
  description: string;
  reporter_name: string;
  reporter_guard_id: string;
  location: string | null;
  occurred_at: string | null;
  created_at: string;
  status: string;
  has_photo: boolean;
}

export interface LinkedEvidence {
  evidence_id: string;
  kind: string;
  title: string;
  camera_name: string;
  covers_from: string;
  covers_to: string;
  saved_by_name: string;
  saved_at: string;
  media_type: string;
}

export interface Participant {
  user_id: string;
  display_id: string;
  full_name: string;
  invited: boolean;
}

export interface FootageGap {
  camera_id: string | null;
  camera_name: string;
  window_from: string;
  window_to: string;
  issue: "NO_RECORDING" | "PARTIAL_RECORDING" | "CAMERA_OFFLINE" | "CAMERA_REMOVED";
  covered_seconds: number;
}

export interface IncidentEvent {
  id: string;
  kind: string;
  actor_name: string;
  actor_role: string;
  from_status: string | null;
  to_status: string | null;
  note: string | null;
  details: Record<string, unknown>;
  occurred_at: string;
}

export interface PersonSearchLink {
  result_id: string;
  search_id: string;
  camera_name: string;
  location: string | null;
  observed_at: string;
  similarity: number;
  band: string;
  review: string;
  reviewed_by_name: string | null;
  has_crop: boolean;
}

export interface IncidentDetail {
  incident: Incident;
  alerts: LinkedAlert[];
  reports: LinkedReport[];
  evidence: LinkedEvidence[];
  participants: Participant[];
  footage_gaps: FootageGap[];
  history: IncidentEvent[];
  person_search: PersonSearchLink[];
  ai_assistance_available: boolean;
}

export interface IncidentMessage {
  id: string;
  sender_user_id: string | null;
  sender_name: string;
  sender_role: string;
  body: string;
  created_at: string;
}

export interface StaffMemberRef {
  user_id: string;
  display_id: string;
  full_name: string;
  role: string;
}

export interface IncidentBody {
  title: string;
  description: string;
  priority: IncidentPriority;
  occurred_at: string | null;
  camera_id: string | null;
  location: { building_id: string; floor_id: string | null; area_id: string | null } | null;
}

export interface CreateIncidentBody extends IncidentBody {
  assigned_to: string | null;
  alert_ids: string[];
  report_ids: string[];
  evidence_ids: string[];
}

export interface IncidentQuery {
  status?: IncidentStatus[];
  priority?: IncidentPriority | "";
  assigned?: string;
  q?: string;
  since?: string;
  until?: string;
  limit: number;
  offset: number;
}

export type LinkKind = "alerts" | "reports" | "evidence";

const BASE = "/incidents";

export const incidents = {
  list: (query: IncidentQuery) => api<IncidentPage>(BASE, { query: { ...query } }),
  get: (id: string) => api<IncidentDetail>(`${BASE}/${id}`),
  create: (body: CreateIncidentBody) => api<Incident>(BASE, { json: body }),
  update: (id: string, body: IncidentBody) => api<Incident>(`${BASE}/${id}`, { method: "PUT", json: body }),
  setStatus: (id: string, status: IncidentStatus, note: string | null) =>
    api<Incident>(`${BASE}/${id}/status`, { json: { status, note } }),
  assign: (id: string, user_id: string | null) =>
    api<Incident>(`${BASE}/${id}/assignee`, { method: "PUT", json: { user_id } }),
  addNote: (id: string, note: string) => api<void>(`${BASE}/${id}/notes`, { json: { note } }),
  link: (id: string, kind: LinkKind, targetId: string) =>
    api<Incident>(`${BASE}/${id}/${kind}/${targetId}`, { method: "PUT" }),
  unlink: (id: string, kind: LinkKind, targetId: string) =>
    api<Incident>(`${BASE}/${id}/${kind}/${targetId}`, { method: "DELETE" }),
  invite: (id: string, guardUserId: string) =>
    api<void>(`${BASE}/${id}/participants/${guardUserId}`, { method: "PUT" }),
  uninvite: (id: string, guardUserId: string) =>
    api<void>(`${BASE}/${id}/participants/${guardUserId}`, { method: "DELETE" }),
  messages: (id: string) => api<IncidentMessage[]>(`${BASE}/${id}/messages`),
  post: (id: string, body: string) => api<IncidentMessage>(`${BASE}/${id}/messages`, { json: { body } }),
  staff: () => api<StaffMemberRef[]>(`${BASE}/assignable-staff`),
  forAlert: (alertId: string) => api<Incident[]>(`${BASE}/for-alert/${alertId}`),
};

export const STATUS_HELP: Record<IncidentStatus, string> = {
  OPEN: "Opened, not yet being investigated.",
  UNDER_INVESTIGATION: "Being looked into.",
  RESPONDING: "Staff or Guards are acting on it.",
  RESOLVED: "Handled; closed with a note.",
  DISMISSED: "Not a real incident; closed with a note.",
};

export const GAP_TEXT: Record<FootageGap["issue"], string> = {
  NO_RECORDING: "No recording is kept for this moment (the camera was not recording, or retention deleted it).",
  PARTIAL_RECORDING: "Only part of this moment is recorded.",
  CAMERA_OFFLINE: "This camera is offline now.",
  CAMERA_REMOVED: "This camera was removed; its footage is no longer available.",
};

export const EVENT_TEXT: Record<string, string> = {
  CREATED: "opened the case",
  DETAILS_UPDATED: "changed the details",
  STATUS_CHANGED: "changed the status",
  ASSIGNED: "assigned the case",
  UNASSIGNED: "removed the assignee",
  NOTE_ADDED: "added a note",
  ALERT_LINKED: "linked an alert",
  ALERT_UNLINKED: "unlinked an alert",
  REPORT_LINKED: "linked a manual report",
  REPORT_UNLINKED: "unlinked a manual report",
  EVIDENCE_LINKED: "linked evidence",
  EVIDENCE_UNLINKED: "unlinked evidence",
  PERSON_SEARCH_LINKED: "linked a verified person-search result",
  PERSON_SEARCH_REVIEWED: "reviewed a person-search result",
  PARTICIPANT_ADDED: "invited a Guard to the chat",
  PARTICIPANT_REMOVED: "removed a Guard from the chat",
};
