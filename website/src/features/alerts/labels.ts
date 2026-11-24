import type { Alert, AlertEvent } from "./types";
import { formatLocationPath } from "../../lib/format";

export const TYPE_OPTIONS = [
  ["WEAPON", "Weapon detected"],
  ["FIGHT_VIOLENCE", "Fight / violence"],
  ["RESTRICTED_AREA", "Restricted area"],
] as const;

export const STATUS_OPTIONS = [
  ["NEW", "New"],
  ["ACKNOWLEDGED", "Acknowledged"],
  ["RESPONDING", "Responding"],
  ["ESCALATED", "Escalated"],
  ["RESOLVED", "Resolved"],
  ["DISMISSED", "Dismissed"],
] as const;

export const ROLE_LABELS: Record<string, string> = {
  ADMIN: "Admin",
  SUPERVISOR: "Supervisor",
  GUARD: "Guard",
  SYSTEM: "System",
};

export const REASON_LABELS: Record<string, string> = {
  LOCATION: "Assigned at the camera's location",
  ESCALATION: "Added by escalation",
  AUTOMATIC: "Added by automatic escalation",
  ASSISTANCE: "Added by an assistance broadcast",
};

export const RECIPIENT_STATE_LABELS: Record<string, string> = {
  NOTIFIED: "Notified, not acknowledged",
  ACKNOWLEDGED: "Acknowledged",
  RESPONDING: "Responding",
};

export const ASSESSMENT_LABELS: Record<string, string> = {
  UNDER_CONTROL: "Under control",
  ADDITIONAL_GUARDS_REQUIRED: "Additional Guards required",
};

const EVENT_LABELS: Record<string, string> = {
  CREATED: "Alert raised by detection",
  DETECTION_REPEATED: "Detection repeated",
  ROUTED: "Sent to Guards",
  ACKNOWLEDGED: "Acknowledged",
  RESPONDING: "Responding",
  ASSESSMENT: "Guard assessment",
  ESCALATED: "Escalated",
  RESOLVED: "Resolved",
  DISMISSED: "Dismissed",
  AUTO_ESCALATED: "Escalated automatically (not acknowledged in time)",
  ASSISTANCE_BROADCAST: "Assistance broadcast to more Guards",
  HANDLER_ASSIGNED: "Handler changed",
};

const SCOPE_LABELS: Record<string, string> = {
  CAMPUS: "every Guard of the campus",
  LOCATION: "Guards of a location",
  GUARDS: "chosen Guards",
  AUTOMATIC: "nearby and campus Guards automatically",
  ASSISTANCE: "more Guards on a Guard's request",
};

export function eventLabel(e: AlertEvent): string {
  return EVENT_LABELS[e.event_type] ?? e.event_type.replace(/_/g, " ").toLowerCase();
}

/** A short, factual summary of an event's details (never invented). */
export function eventDetails(e: AlertEvent): string {
  const d = e.details;
  const parts: string[] = [];
  // Only an assessment's action adds information; acknowledge/responding repeat the event name.
  if (e.event_type === "ASSESSMENT" && typeof d.action === "string") parts.push(ASSESSMENT_LABELS[d.action] ?? d.action.replace(/_/g, " ").toLowerCase());
  if (typeof d.guards === "number") parts.push(`${d.guards} Guard${d.guards === 1 ? "" : "s"}`);
  if (d.camera_has_location === false) parts.push("the camera has no location, so no Guard was routed");
  if (typeof d.scope === "string") parts.push(`to ${SCOPE_LABELS[d.scope] ?? d.scope}`);
  if (typeof d.added_guards === "number") parts.push(`${d.added_guards} Guard${d.added_guards === 1 ? "" : "s"} added`);
  if (d.confidence !== undefined && d.confidence !== null) parts.push(`confidence ${formatConfidence(String(d.confidence))}`);
  if (d.note_given === true) parts.push("with a note");
  if (typeof d.after_seconds === "number") parts.push(`after ${Math.round(d.after_seconds / 60 * 10) / 10} min without acknowledgement`);
  if (e.event_type === "HANDLER_ASSIGNED") parts.push(typeof d.handler === "string" ? `now ${d.handler}` : "cleared");
  return parts.join(" · ");
}

export function formatConfidence(value: string | null): string {
  if (value === null || value === "") return "Not provided";
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n * 1000) / 10}%` : value;
}

export function alertLocation(a: Alert): string {
  return formatLocationPath(a.location.building_name, a.location.floor_name, a.location.area_name);
}

export function isClosed(a: Alert): boolean {
  return a.status === "RESOLVED" || a.status === "DISMISSED";
}
