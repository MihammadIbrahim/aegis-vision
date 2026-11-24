// Shapes of /alerts (decision D-013). Values are the exact strings the Core API returns.

export type AlertType = "WEAPON" | "FIGHT_VIOLENCE" | "RESTRICTED_AREA";
export type AlertPriority = "HIGH" | "STANDARD";
export type AlertStatus = "NEW" | "ACKNOWLEDGED" | "RESPONDING" | "ESCALATED" | "RESOLVED" | "DISMISSED";
export type EscalationScope = "CAMPUS" | "LOCATION" | "GUARDS";

export const CLOSED_STATUSES: readonly AlertStatus[] = ["RESOLVED", "DISMISSED"];

export interface AlertRecipient {
  guard_id: string;
  full_name: string;
  reason: "LOCATION" | "ESCALATION" | string;
  state: "NOTIFIED" | "ACKNOWLEDGED" | "RESPONDING" | string;
  assessment: "UNDER_CONTROL" | "ADDITIONAL_GUARDS_REQUIRED" | null;
  acknowledged_at: string | null;
  responding_at: string | null;
}

export interface AlertLocation {
  building_id: string | null;
  building_name: string | null;
  building_kind: "INDOOR" | "OUTDOOR_PERIMETER" | string | null;
  floor_id: string | null;
  floor_name: string | null;
  area_id: string | null;
  area_name: string | null;
}

export interface Alert {
  id: string;
  alert_type: AlertType;
  priority: AlertPriority;
  status: AlertStatus;
  camera_id: string;
  camera_name: string;
  location: AlertLocation;
  /** A decimal between 0 and 1, sent as a string; null when the detector gave none. */
  confidence: string | null;
  detected_at: string;
  last_detected_at: string;
  detection_count: number;
  close_note: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  /** Filled by GET /alerts/{id} only; the list endpoint returns an empty array. */
  recipients: AlertRecipient[];
  /** The Admin or Supervisor responsible for the alert (D-024). */
  handled_by?: string | null;
  handled_by_name?: string | null;
  handled_at?: string | null;
  /** Guards the alert went to, and how many acknowledged / are responding. */
  recipient_count?: number;
  acknowledged_count?: number;
  responding_count?: number;
  /** A detection snapshot is kept for the alert. */
  has_snapshot?: boolean;
}

/** One escalation of an alert: manual (by a person) or AUTOMATIC / ASSISTANCE (by the system). */
export interface AlertEscalation {
  id: string;
  scope: EscalationScope | "AUTOMATIC" | "ASSISTANCE" | string;
  escalated_by_name: string | null;
  escalated_by_role: string | null;
  note: string | null;
  added_count: number;
  created_at: string;
  guards: string[];
}

export interface AlertEvent {
  event_type: string;
  actor_role: string;
  actor_name: string | null;
  from_status: string | null;
  to_status: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface AlertMessage {
  id: string;
  sender_role: string;
  sender_name: string;
  body: string;
  created_at: string;
}

export interface AlertQuery {
  /** Alert ID, type, camera or location. */
  q?: string;
  /** ISO date-times bounding when the alert was raised. */
  since?: string;
  until?: string;
  /** An Admin or Supervisor's account ID. */
  handled_by?: string;
  status?: string;
  priority?: string;
  alert_type?: string;
  building_id?: string;
  floor_id?: string;
  area_id?: string;
  limit?: number;
  offset?: number;
}

export interface EscalationBody {
  scope: EscalationScope;
  location?: { building_id: string; floor_id: string | null; area_id: string | null } | null;
  guard_ids?: string[];
  note?: string | null;
}
