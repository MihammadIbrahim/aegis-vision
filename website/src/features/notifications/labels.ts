export const KIND_LABELS: Record<string, string> = {
  ALERT_NEW: "New alert",
  ALERT_ESCALATED: "Alert escalated",
  ALERT_RESPONDING: "Guard responding",
  ALERT_ASSESSMENT: "Guard assessment",
  ALERT_ASSISTANCE_REQUESTED: "Additional Guards requested",
  ALERT_RESOLVED: "Alert resolved",
  ALERT_DISMISSED: "Alert dismissed",
  MANUAL_REPORT_SUBMITTED: "Manual report submitted",
  GUARD_UPDATED: "Guard updated by a Supervisor",
  GUARD_PASSWORD_SET: "Guard password set by a Supervisor",
  GUARD_LOCATION_CHANGED: "Guard location changed by a Supervisor",
  // Decision D-023: camera health from the ingestion service, disk warnings from the retention daemon.
  CAMERA_OFFLINE: "Camera offline",
  CAMERA_ONLINE: "Camera back online",
  STORAGE_WARNING: "Recording storage low",
  STORAGE_CRITICAL: "Recording storage critical",
  // Decision D-024.
  INCIDENT_ASSIGNED: "Case assigned to you",
  INCIDENT_STATUS_CHANGED: "Case status changed",
  INCIDENT_INVITED: "Added to a case chat",
  ALERT_AUTO_ESCALATED: "Alert escalated automatically",
  ALERT_ASSISTANCE_BROADCAST: "Assistance requested from Guards",
  ALERT_HANDLER_ASSIGNED: "You are handling an alert",
  GUARD_ID_REGENERATED: "Guard ID regenerated",
};

export const KIND_OPTIONS = Object.entries(KIND_LABELS);

/** Where a notification leads: its alert, manual report or Guard; null when there is nothing to open. */
export function notificationTarget(n: {
  alert_id: string | null;
  manual_report_id: string | null;
  subject_user_id?: string | null;
  camera_id?: string | null;
  incident_id?: string | null;
  kind?: string;
}): { to: string; label: string } | null {
  if (n.incident_id) return { to: `/incidents/${n.incident_id}`, label: "Open case" };
  if (n.alert_id) return { to: `/alerts/${n.alert_id}`, label: "Open alert" };
  if (n.manual_report_id) return { to: `/manual-reports/${n.manual_report_id}`, label: "Open report" };
  if (n.subject_user_id) return { to: `/staff/${n.subject_user_id}`, label: "Open Guard" };
  if (n.camera_id) return { to: "/live", label: "Open live cameras" };
  if (n.kind === "STORAGE_WARNING" || n.kind === "STORAGE_CRITICAL") return { to: "/recordings", label: "Open recordings" };
  return null;
}
