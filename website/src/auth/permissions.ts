import type { LucideIcon } from "lucide-react";
import {
  Archive,
  BarChart3,
  Bell,
  BotMessageSquare,
  BrainCircuit,
  Building2,
  Camera,
  ClipboardList,
  FileSearch,
  FileUp,
  Film,
  FolderOpen,
  History,
  Inbox,
  LayoutDashboard,
  ScanSearch,
  Settings,
  ShieldCheck,
  Siren,
  SlidersHorizontal,
  UserCircle,
  UserCog,
  UserRoundSearch,
  Users,
  Video,
} from "lucide-react";

import type { Role } from "../api/types";

// What each role is offered in the UI (decision D-016). The server enforces the same rules, so
// hiding an action here is a convenience, never the protection.

/** Register, edit, deactivate, import, configure, grant and revoke access. */
export function canManagePersonnel(role: Role | undefined): boolean {
  return role === "ADMIN";
}

/** The read-only personnel directory, basic details and restricted-area authorizations. */
export function canViewPersonnel(role: Role | undefined): boolean {
  return role === "ADMIN" || role === "SUPERVISOR";
}

/** Alerts, manual reports and notifications of the own campus (decision D-013): view, resolve,
 * dismiss, escalate, alert chat and report review. Guards use the mobile app instead. */
export function canManageAlerts(role: Role | undefined): boolean {
  return role === "ADMIN" || role === "SUPERVISOR";
}

/** The read-only AI Investigation & Security Analytics Chatbot (decision D-027): Admins (whole campus) and
 * Supervisors (operational scope). Guards and the Super Admin have no access (the API answers 403). */
export function canUseAssistant(role: Role | undefined): boolean {
  return role === "ADMIN" || role === "SUPERVISOR";
}

/** Buildings and outdoor perimeters: add, edit, remove (decision D-010). Supervisors only view them. */
export function canManageBuildings(role: Role | undefined): boolean {
  return role === "ADMIN";
}

/** Floors, areas and cameras of the own campus (decisions D-010, D-012): Admins and Supervisors. */
export function canManageSite(role: Role | undefined): boolean {
  return role === "ADMIN" || role === "SUPERVISOR";
}

/** Staff Management (decision D-017): list and view Guards, update Guard details, set Guard
 * passwords and change Guard locations. Admins and Supervisors. */
export function canManageStaff(role: Role | undefined): boolean {
  return role === "ADMIN" || role === "SUPERVISOR";
}

/** Add Guards and Supervisors, see Supervisors, deactivate and reactivate accounts: Admins only. */
export function canAdministerStaff(role: Role | undefined): boolean {
  return role === "ADMIN";
}

export interface NavItem {
  to: string;
  label: string;
  adminOnly: boolean;
  icon: LucideIcon;
  /** Only the organization's Main Admin (campus approvals). */
  mainAdminOnly?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

// Only modules whose screens exist appear here (no placeholders for unbuilt backend features).
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Operations",
    items: [
      { to: "/dashboard", label: "Dashboard", adminOnly: false, icon: LayoutDashboard },
      { to: "/alerts", label: "Alerts", adminOnly: false, icon: Siren },
      { to: "/incidents", label: "Incidents", adminOnly: false, icon: FolderOpen },
      // Decision D-027: the read-only AI Investigation & Security Analytics Chatbot.
      { to: "/assistant", label: "AI Investigation", adminOnly: false, icon: BotMessageSquare },
      { to: "/manual-reports", label: "Manual Reports", adminOnly: false, icon: ClipboardList },
      { to: "/notifications", label: "Notifications", adminOnly: false, icon: Bell },
    ],
  },
  {
    // Decision D-023: live monitoring, routine recordings with retention, and the evidence vault.
    label: "Video & Evidence",
    items: [
      { to: "/live", label: "Live Cameras", adminOnly: false, icon: Video },
      { to: "/recordings", label: "Recordings", adminOnly: false, icon: Film },
      { to: "/evidence", label: "Evidence Vault", adminOnly: false, icon: Archive },
      { to: "/person-search", label: "Person Search", adminOnly: false, icon: ScanSearch },
    ],
  },
  {
    label: "Site & Infrastructure",
    items: [
      { to: "/locations", label: "Buildings & Locations", adminOnly: false, icon: Building2 },
      { to: "/cameras", label: "Cameras", adminOnly: false, icon: Camera },
      { to: "/ai-configuration", label: "AI Configuration", adminOnly: false, icon: BrainCircuit },
    ],
  },
  {
    label: "Staff",
    items: [
      { to: "/staff", label: "Staff Management", adminOnly: false, icon: UserCog },
      { to: "/user-records", label: "User Records", adminOnly: true, icon: UserRoundSearch },
    ],
  },
  {
    label: "People",
    items: [
      { to: "/personnel", label: "Personnel Directory", adminOnly: false, icon: Users },
      { to: "/restricted-access", label: "Restricted-Area Access", adminOnly: false, icon: ShieldCheck },
    ],
  },
  {
    label: "Personnel administration",
    items: [
      { to: "/personnel/imports/new", label: "Bulk Import", adminOnly: true, icon: FileUp },
      { to: "/personnel/imports", label: "Import History", adminOnly: true, icon: History },
      { to: "/personnel/settings", label: "Categories & Fields", adminOnly: true, icon: SlidersHorizontal },
    ],
  },
  {
    label: "Reports & oversight",
    items: [
      { to: "/reports", label: "Reports & Analytics", adminOnly: false, icon: BarChart3 },
      { to: "/audit-logs", label: "Audit Logs", adminOnly: false, icon: FileSearch },
    ],
  },
  {
    label: "Account & organization",
    items: [
      { to: "/campus-requests", label: "Campus Requests", adminOnly: true, mainAdminOnly: true, icon: Inbox },
      { to: "/organization/settings", label: "Organization Settings", adminOnly: true, icon: Settings },
      { to: "/organization/account", label: "Account & Ownership", adminOnly: true, icon: History },
      { to: "/profile", label: "Profile", adminOnly: false, icon: UserCircle },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

function visible(item: NavItem, role: Role | undefined, isMainAdmin: boolean): boolean {
  return (!item.adminOnly || canManagePersonnel(role)) && (!item.mainAdminOnly || isMainAdmin);
}

/** The groups a role sees; empty groups are left out. Campus Requests appear for the Main Admin only. */
export function navGroupsFor(role: Role | undefined, isMainAdmin = false): NavGroup[] {
  if (!canViewPersonnel(role)) return [];
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => visible(item, role, isMainAdmin)),
  })).filter((group) => group.items.length > 0);
}

export function navFor(role: Role | undefined, isMainAdmin = false): NavItem[] {
  if (!canViewPersonnel(role)) return [];
  return NAV_ITEMS.filter((item) => visible(item, role, isMainAdmin));
}
