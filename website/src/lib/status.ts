// One place that decides how every status looks: a tone (colour family from tokens.css) and a text
// label. Status is never shown by colour alone. Values are the exact strings the Core API returns
// (alert entity, manual reports, personnel, access grants, imports). The icon repeats the meaning for
// quick scanning and is hidden from screen readers (the label is read instead).
import {
  Archive,
  Ban,
  Bell,
  LogOut,
  MapPin,
  Radio,
  Building2,
  Mail,
  MailOpen,
  Siren,
  Circle,
  CircleArrowUp,
  CircleCheck,
  CircleDot,
  CircleHelp,
  CircleX,
  DoorClosed,
  Eye,
  FileText,
  Footprints,
  Lock,
  type LucideIcon,
  ShieldAlert,
  Trees,
  Undo2,
  Upload,
  UsersRound,
} from "lucide-react";

export type Tone =
  | "normal"
  | "new"
  | "acknowledged"
  | "responding"
  | "resolved"
  | "warning"
  | "unauthorized"
  | "critical"
  | "unknown"
  | "inactive";

export interface StatusLook {
  tone: Tone;
  label: string;
  icon: LucideIcon;
}

const LOOKS: Record<string, StatusLook> = {
  // Alert lifecycle (AlertStatus): New → Acknowledged → Responding → Resolved; plus Escalated and Dismissed.
  NEW: { tone: "new", label: "New", icon: CircleDot },
  ACKNOWLEDGED: { tone: "acknowledged", label: "Acknowledged", icon: Eye },
  RESPONDING: { tone: "responding", label: "Responding", icon: Footprints },
  ESCALATED: { tone: "warning", label: "Escalated", icon: CircleArrowUp },
  RESOLVED: { tone: "resolved", label: "Resolved", icon: CircleCheck },
  DISMISSED: { tone: "inactive", label: "Dismissed", icon: Ban },
  // Alert types (AlertType): red only for the two HIGH-priority types.
  WEAPON: { tone: "critical", label: "Weapon detected", icon: ShieldAlert },
  FIGHT_VIOLENCE: { tone: "critical", label: "Fight / violence", icon: UsersRound },
  RESTRICTED_AREA: { tone: "unauthorized", label: "Restricted area", icon: DoorClosed },
  // Alert priority: WEAPON and FIGHT_VIOLENCE are always HIGH.
  HIGH: { tone: "critical", label: "High priority", icon: Siren },
  STANDARD: { tone: "normal", label: "Standard", icon: Circle },
  // Alert recipients (a Guard's own progress on an alert).
  NOTIFIED: { tone: "new", label: "Notified", icon: Bell },
  // Notifications.
  UNREAD: { tone: "new", label: "Unread", icon: Mail },
  READ: { tone: "inactive", label: "Read", icon: MailOpen },
  // Identity: uncertainty is not danger.
  UNKNOWN_IDENTITY: { tone: "unknown", label: "Unknown identity", icon: CircleHelp },
  // Manual reports.
  SUBMITTED: { tone: "new", label: "Submitted", icon: FileText },
  REVIEWED: { tone: "resolved", label: "Reviewed", icon: CircleCheck },
  // Personnel and access grants.
  ACTIVE: { tone: "resolved", label: "Active", icon: CircleCheck },
  INACTIVE: { tone: "inactive", label: "Inactive", icon: Ban },
  REVOKED: { tone: "inactive", label: "Revoked", icon: Ban },
  RESTRICTED: { tone: "warning", label: "Restricted", icon: Lock },
  // Imports.
  UPLOADED: { tone: "new", label: "Uploaded", icon: Upload },
  VALIDATED: { tone: "resolved", label: "Validated", icon: CircleCheck },
  INVALID: { tone: "critical", label: "Invalid", icon: CircleX },
  IMPORTED: { tone: "resolved", label: "Imported", icon: CircleCheck },
  FAILED: { tone: "critical", label: "Failed", icon: CircleX },
  ROLLED_BACK: { tone: "inactive", label: "Rolled back", icon: Undo2 },
  DISCARDED: { tone: "inactive", label: "Discarded", icon: Archive },
  // Locations and cameras: a description, not a state, so the neutral tone.
  INDOOR: { tone: "normal", label: "Indoor", icon: Building2 },
  OUTDOOR: { tone: "normal", label: "Outdoor", icon: Trees },
  OUTDOOR_PERIMETER: { tone: "normal", label: "Outdoor perimeter", icon: Trees },
  // Staff accounts (decision D-017): "Left" = deactivated; Guard categories.
  LEFT: { tone: "inactive", label: "Left", icon: LogOut },
  STANDBY: { tone: "responding", label: "Standby", icon: Radio },
  STATIONED: { tone: "acknowledged", label: "Stationed", icon: MapPin },
  // Camera connection (decision D-023): reported by the ingestion service or a health check.
  ONLINE: { tone: "resolved", label: "Online", icon: Radio },
  OFFLINE: { tone: "critical", label: "Offline", icon: CircleX },
  UNKNOWN: { tone: "unknown", label: "Unknown", icon: CircleHelp },
  // Evidence vault.
  CLIP: { tone: "normal", label: "Video clip", icon: FileText },
  SNAPSHOT: { tone: "normal", label: "Snapshot", icon: Eye },
  // Incident cases (decision D-024): Open → Under investigation → Responding → Resolved / Dismissed.
  OPEN: { tone: "new", label: "Open", icon: CircleDot },
  UNDER_INVESTIGATION: { tone: "acknowledged", label: "Under investigation", icon: Eye },
  // Campus requests.
  PENDING: { tone: "new", label: "Pending", icon: CircleDot },
  APPROVED: { tone: "resolved", label: "Approved", icon: CircleCheck },
  REJECTED: { tone: "inactive", label: "Rejected", icon: Ban },
  // Audit outcomes.
  SUCCESS: { tone: "resolved", label: "Success", icon: CircleCheck },
  FAILURE: { tone: "critical", label: "Failure", icon: CircleX },
  DENIED: { tone: "warning", label: "Denied", icon: Ban },
  // Person Search: resemblances, never identities.
  LIKELY_MATCH: { tone: "acknowledged", label: "Likely match", icon: CircleDot },
  UNCERTAIN: { tone: "unknown", label: "Uncertain", icon: CircleHelp },
  VERIFIED_MATCH: { tone: "resolved", label: "Verified match", icon: CircleCheck },
  NOT_A_MATCH: { tone: "inactive", label: "Not a match", icon: Ban },
  // Implementation status on the public landing page (never presents planned work as available).
  AVAILABLE: { tone: "resolved", label: "Available", icon: CircleCheck },
  PARTIAL: { tone: "acknowledged", label: "Partly built", icon: CircleDot },
  PLANNED: { tone: "unknown", label: "Planned", icon: CircleHelp },
};

export function statusLook(status: string): StatusLook {
  return LOOKS[status] ?? { tone: "normal", label: status.replace(/_/g, " ").toLowerCase(), icon: Circle };
}
