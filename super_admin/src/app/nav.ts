import type { LucideIcon } from "lucide-react";
import { Activity, BrainCircuit, Building2, LayoutDashboard, ScrollText, UserCog } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Platform",
    items: [
      { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { to: "/organizations", label: "Organizations", icon: Building2 },
    ],
  },
  {
    label: "Operations",
    items: [
      { to: "/ai-settings", label: "AI Settings", icon: BrainCircuit },
      { to: "/health", label: "System Health", icon: Activity },
      { to: "/audit-logs", label: "Audit Logs", icon: ScrollText },
    ],
  },
  { label: "Account", items: [{ to: "/profile", label: "Profile & Security", icon: UserCog }] },
];
