import type {
  AlertLocation,
  AlertPriority,
  AlertResponse,
  AlertStatus,
  AlertType,
  IncidentStatus,
  OperationalMode,
  OwnGuardLocationResponse,
  ParticipantState,
  RecipientReason,
  RecipientResponse,
  StaffRole,
} from "../api/types";

export const ALERT_TYPE_LABEL: Record<AlertType, string> = {
  WEAPON: "Weapon Detection",
  FIGHT_VIOLENCE: "Fight / Violence Detection",
  RESTRICTED_AREA: "Restricted-Area Alert",
};

export const PRIORITY_LABEL: Record<AlertPriority, string> = { HIGH: "High priority", STANDARD: "Standard priority" };

export const STATUS_LABEL: Record<AlertStatus, string> = {
  NEW: "New",
  ACKNOWLEDGED: "Acknowledged",
  RESPONDING: "Responding",
  ESCALATED: "Supervisor escalated",
  RESOLVED: "Resolved",
  DISMISSED: "Dismissed",
};

export const INCIDENT_STATUS_LABEL: Record<IncidentStatus, string> = {
  OPEN: "Open",
  UNDER_INVESTIGATION: "Under investigation",
  RESPONDING: "Responding",
  RESOLVED: "Resolved",
  DISMISSED: "Dismissed",
};

export const REASON_LABEL: Record<RecipientReason, string> = {
  LOCATION: "Your post",
  ESCALATION: "Escalated to you",
  AUTOMATIC: "Escalated: nobody acknowledged in time",
  ASSISTANCE: "Assistance requested by a Guard",
};

export const ROLE_LABEL: Record<StaffRole, string> = { ADMIN: "Admin", SUPERVISOR: "Supervisor", GUARD: "Guard" };

export const MODE_LABEL: Record<OperationalMode, string> = { STANDBY: "Standby", STATIONED: "Stationed / Standalone" };

export const PARTICIPANT_LABEL: Record<ParticipantState, string> = {
  INVITED: "Ringing",
  JOINED: "In the call",
  DECLINED: "Declined",
  LEFT: "Left",
  MISSED: "Missed",
};

export function isClosed(status: AlertStatus | IncidentStatus): boolean {
  return status === "RESOLVED" || status === "DISMISSED";
}

/** The Guard's own recipient entry (the API returns only that one to a Guard). */
export function myRecipient(alert: AlertResponse): RecipientResponse | null {
  return alert.recipients[0] ?? null;
}

/** An open alert this Guard has not acknowledged yet: the alarm rings for it. */
export function needsAcknowledgement(alert: AlertResponse): boolean {
  const mine = myRecipient(alert);
  return !isClosed(alert.status) && mine !== null && mine.state === "NOTIFIED";
}

/** Reached through an escalation, an automatic escalation or an assistance broadcast: "I'm Responding" applies. */
export function isEscalatedToMe(alert: AlertResponse): boolean {
  const reason = myRecipient(alert)?.reason;
  return reason === "ESCALATION" || reason === "AUTOMATIC" || reason === "ASSISTANCE";
}

export function alertLocationText(location: AlertLocation): string {
  const parts = [location.building_name, location.floor_name, location.area_name].filter(Boolean);
  return parts.length ? parts.join(" → ") : "No location recorded";
}

export function ownLocationText(location: OwnGuardLocationResponse | undefined | null): string {
  if (!location || !location.assigned) return "No post (Standby)";
  const parts = [location.building_name, location.floor_name, location.area_name].filter(Boolean);
  let text = parts.join(" → ");
  if (location.boundary_direction) {
    const side = location.boundary_side ? ` (${location.boundary_side === "INSIDE" ? "inside" : "outside"})` : "";
    const name = location.boundary_direction === "CUSTOM" ? location.boundary_name : location.boundary_direction;
    text += ` · ${name ? name.charAt(0) + name.slice(1).toLowerCase() : ""} boundary${side}`;
  }
  return text;
}

export function confidenceText(confidence: string | null): string | null {
  if (confidence === null) return null;
  const value = Number(confidence);
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : null;
}

export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

const pad = (n: number) => String(n).padStart(2, "0");

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

/** Most urgent first: high priority, then the newest detection. */
export function byUrgency(a: AlertResponse, b: AlertResponse): number {
  if (a.priority !== b.priority) return a.priority === "HIGH" ? -1 : 1;
  return new Date(b.last_detected_at).getTime() - new Date(a.last_detected_at).getTime();
}
