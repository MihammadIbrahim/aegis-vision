// One place that decides how every status looks: a tone (colour family from tokens.css) and a text
// label. Status is never shown by colour alone. Values are the exact strings the Core API returns
// (platform_management_dto.py). The icon repeats the meaning and is hidden from screen readers.
import {
  Ban,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleX,
  Circle,
  CirclePause,
  type LucideIcon,
  ShieldCheck,
  TriangleAlert,
  Bot,
  UserRound,
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
  // Organization status.
  ACTIVE: { tone: "resolved", label: "Active", icon: CircleCheck },
  INACTIVE: { tone: "inactive", label: "Inactive", icon: CirclePause },
  SUSPENDED: { tone: "critical", label: "Suspended", icon: Ban },
  // Audit outcome.
  SUCCESS: { tone: "resolved", label: "Success", icon: CircleCheck },
  FAILURE: { tone: "critical", label: "Failure", icon: CircleX },
  // Health: services and overall.
  OPERATIONAL: { tone: "resolved", label: "Operational", icon: CircleCheck },
  DEGRADED: { tone: "warning", label: "Degraded", icon: TriangleAlert },
  DOWN: { tone: "critical", label: "Down", icon: CircleX },
  NOT_DEPLOYED: { tone: "inactive", label: "Not deployed", icon: CircleDashed },
  // Storage level.
  OK: { tone: "resolved", label: "OK", icon: CircleCheck },
  WARNING: { tone: "warning", label: "Warning", icon: TriangleAlert },
  CRITICAL: { tone: "critical", label: "Critical", icon: CircleAlert },
  UNAVAILABLE: { tone: "unknown", label: "Unavailable", icon: CircleDashed },
  // Audit actors.
  SUPER_ADMIN: { tone: "acknowledged", label: "Super Admin", icon: ShieldCheck },
  ORGANIZATION_ADMIN: { tone: "normal", label: "Organization Admin", icon: UserRound },
  SYSTEM: { tone: "inactive", label: "System", icon: Bot },
  // AI modules.
  ENABLED: { tone: "resolved", label: "Enabled", icon: CircleCheck },
  DISABLED: { tone: "inactive", label: "Disabled", icon: CirclePause },
  PLANNED: { tone: "unknown", label: "Not built yet", icon: CircleDashed },
  // Per-organization AI feature access (decision D-028).
  ALLOWED: { tone: "resolved", label: "Allowed", icon: CircleCheck },
  BLOCKED: { tone: "critical", label: "Blocked for this organization", icon: Ban },
  DISABLED_GLOBALLY: { tone: "inactive", label: "Disabled globally", icon: CirclePause },
};

export function statusLook(status: string): StatusLook {
  return LOOKS[status] ?? { tone: "normal", label: status.replace(/_/g, " ").toLowerCase(), icon: Circle };
}
