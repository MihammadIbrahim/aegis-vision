// GET /organization-admin/dashboard and GET /supervisor/dashboard. Both carry only the caller's own
// organization and campus (the request takes no IDs).
//
// Their ``not_available_yet`` list predates the alert module (D-013) and still names "alerts" and
// "guard_reports"; the dashboard does not use it. Alert and report figures come from those modules'
// own endpoints instead.

export interface DashboardOrganization {
  organization_id: string;
  name: string;
  status: string;
  official_email?: string | null;
  official_phone_number?: string | null;
}

export interface AdminDashboardData {
  organization: DashboardOrganization;
  campus: { name: string; status: string };
  admin: { user_id: string; full_name: string; is_main_admin: boolean };
  summary: {
    active_admins: number;
    active_supervisors: number;
    active_guards: number;
    cameras: number;
    /** Main Admin only; null for other Admins. */
    pending_campus_requests: number | null;
  };
}

export interface SupervisorDashboardData {
  organization: DashboardOrganization;
  campus: { name: string; status: string };
  supervisor: { user_id: string; full_name: string };
  summary: { active_guards: number; cameras: number };
}
