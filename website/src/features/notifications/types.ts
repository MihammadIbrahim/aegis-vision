// The signed-in user's in-app notifications (decision D-013). Stored and listed only: there is no
// push or e-mail delivery; the page and the topbar count read them from the API.

export type NotificationKind =
  | "ALERT_NEW"
  | "ALERT_ESCALATED"
  | "ALERT_RESPONDING"
  | "ALERT_ASSESSMENT"
  | "ALERT_ASSISTANCE_REQUESTED"
  | "ALERT_RESOLVED"
  | "ALERT_DISMISSED"
  | "MANUAL_REPORT_SUBMITTED"
  // Sent to the campus's Admins when a Supervisor changes a Guard (decision D-017).
  | "GUARD_UPDATED"
  | "GUARD_PASSWORD_SET"
  | "GUARD_LOCATION_CHANGED"
  // Decision D-023.
  | "CAMERA_OFFLINE"
  | "CAMERA_ONLINE"
  | "STORAGE_WARNING"
  | "STORAGE_CRITICAL"
  // Decision D-024.
  | "INCIDENT_ASSIGNED"
  | "INCIDENT_STATUS_CHANGED"
  | "INCIDENT_INVITED"
  | "ALERT_AUTO_ESCALATED"
  | "ALERT_ASSISTANCE_BROADCAST"
  | "ALERT_HANDLER_ASSIGNED"
  | "GUARD_ID_REGENERATED";

export interface AppNotification {
  id: string;
  kind: NotificationKind | string;
  title: string;
  alert_id: string | null;
  manual_report_id: string | null;
  /** The Guard a staff notification is about (internal user ID); null otherwise. */
  subject_user_id: string | null;
  /** The case an INCIDENT_* notification is about (decision D-024). */
  incident_id?: string | null;
  /** The camera a CAMERA_OFFLINE / CAMERA_ONLINE notification is about; null otherwise. */
  camera_id?: string | null;
  created_at: string;
  read_at: string | null;
}
