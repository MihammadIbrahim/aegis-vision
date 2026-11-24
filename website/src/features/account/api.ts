import { api, download } from "../../api/client";
import type { CodeSentLike } from "./components/StepUpDialog";

// The signed-in Admin's or Supervisor's own account, organization settings and the organization page
// (Org Admin pages 14, 24, 25; Supervisor page 19; decisions D-016, D-024).

export interface AdminProfile {
  admin_id: string;
  full_name: string;
  username: string | null;
  email: string;
  phone_number: string | null;
  role: "ADMIN";
  is_main_admin: boolean;
  organization_name: string;
  organization_id: string;
  campus_name: string;
}

export interface SupervisorProfile {
  organization_name: string;
  organization_id: string;
  supervisor_id: string;
  full_name: string;
  username: string | null;
  email: string;
  phone_number: string | null;
}

export interface ProfileChangeBody {
  current_password: string;
  full_name?: string;
  username?: string;
  email?: string;
  phone_number?: string;
  new_password?: string;
  confirm_new_password?: string;
}

export interface ProfileChangeStarted {
  change_id: string;
  email: string;
  new_email: string | null;
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
  expires_at: string;
}

export interface OrganizationSettings {
  profile: {
    name: string;
    organization_code: string;
    official_email: string | null;
    official_phone: string | null;
    registered_at: string;
    address_line: string | null;
    city: string | null;
    country: string | null;
  };
  preferences: { time_zone: string; date_format: string; time_format: string };
  alert_defaults: {
    auto_escalation_enabled: boolean;
    escalation_high_seconds: number;
    escalation_standard_seconds: number;
    assistance_broadcast_enabled: boolean;
  };
  is_main_admin: boolean;
  can_edit: boolean;
  campus: {
    campus_name: string;
    retention_seconds: number;
    retention_label: string;
    retention_is_default: boolean;
    ai_modules: string[];
    pending_campus_requests: number | null;
  };
  security: {
    sign_in_code_required: boolean;
    code_expires_seconds: number;
    code_max_attempts: number;
    lockout_after_failed_sign_ins: number;
    lockout_minutes: number;
    password_min_length: number;
    password_max_length: number;
  };
  campus_requests: {
    decision_window_hours: number;
    requires_exact_name_and_id: boolean;
    applicant_email_code: boolean;
    approval_needs_password_and_code: boolean;
    rejection_needs_password: boolean;
    rejection_erases_details: boolean;
  };
  updated_at: string | null;
  updated_by_name: string | null;
}

export interface SettingsBody {
  official_phone: string | null;
  address_line: string | null;
  city: string | null;
  country: string | null;
  time_zone: string;
  date_format: string;
  time_format: string;
  auto_escalation_enabled: boolean;
  escalation_high_seconds: number;
  escalation_standard_seconds: number;
  assistance_broadcast_enabled: boolean;
  current_password: string;
}

export interface DeletionStatus {
  is_main_admin: boolean;
  can_delete: boolean;
  blocked_by: string | null;
  campus_will_be_deleted: boolean;
  organization_will_be_deleted: boolean;
  transfer_candidates: { admin_id: string; full_name: string }[];
  latest_transfer: { transfer_id: string; status: string; to_name: string; expires_at: string } | null;
}

export interface DeletionStarted extends CodeSentLike {
  export: { optional: true; download_url: string; not_included: string[] };
}

export interface IncomingTransfer {
  transfer_id: string;
  from_name: string;
  created_at: string;
  expires_at: string;
}

export interface OwnershipHistoryEntry {
  transfer_id: string;
  previous_owner_name: string;
  previous_owner_username: string | null;
  previous_owner_email: string;
  status: "ACCEPTED";
  summary: string;
  offered_at: string;
  accepted_at: string;
}

const PROFILE = "/organization-admin/profile";
const SUPERVISOR = "/supervisor/profile";
const ACCOUNT = "/organization-admin/account";
const TRANSFERS = "/organization-admin/ownership-transfers";

export const adminProfile = {
  get: () => api<AdminProfile>(PROFILE),
  start: (body: ProfileChangeBody) => api<ProfileChangeStarted>(`${PROFILE}/changes`, { json: body }),
  resend: (id: string) => api<ProfileChangeStarted>(`${PROFILE}/changes/${id}/resend-otp`, { method: "POST" }),
  verify: (id: string, otp: string, new_email_otp: string | null) =>
    api<{ profile: AdminProfile; signed_out: boolean }>(`${PROFILE}/changes/${id}/verify`, { json: { otp, new_email_otp } }),
  cancel: (id: string) => api<void>(`${PROFILE}/changes/${id}`, { method: "DELETE" }),
  regenerateId: (current_password: string) =>
    api<{ display_id: string }>(`${PROFILE}/regenerate-id`, { json: { current_password } }),
};

export const supervisorProfile = {
  get: () => api<SupervisorProfile>(SUPERVISOR),
  start: (body: ProfileChangeBody) => api<ProfileChangeStarted>(`${SUPERVISOR}/changes`, { json: body }),
  resend: (id: string) => api<ProfileChangeStarted>(`${SUPERVISOR}/changes/${id}/resend-otp`, { method: "POST" }),
  verify: (id: string, otp: string, new_email_otp: string | null) =>
    api<{ profile: SupervisorProfile; signed_out: boolean }>(`${SUPERVISOR}/changes/${id}/verify`, {
      json: { otp, new_email_otp },
    }),
  cancel: (id: string) => api<void>(`${SUPERVISOR}/changes/${id}`, { method: "DELETE" }),
};

export const organization = {
  settings: () => api<OrganizationSettings>("/organization/settings"),
  save: (body: SettingsBody) => api<OrganizationSettings>("/organization/settings", { method: "PUT", json: body }),
  preferences: () => api<OrganizationSettings["preferences"]>("/organization/preferences"),
  directory: () => api<{ admins: { full_name: string; is_first_admin: boolean }[] }>("/organization/admin-directory"),
};

export const account = {
  deletionStatus: () => api<DeletionStatus>(`${ACCOUNT}/deletion`),
  startDeletion: (admin_id: string, current_password: string) =>
    api<DeletionStarted>(`${ACCOUNT}/deletion`, { json: { admin_id, current_password } }),
  resendDeletion: (id: string) => api<CodeSentLike>(`${ACCOUNT}/deletion/${id}/resend-otp`, { method: "POST" }),
  completeDeletion: (id: string, otp: string) =>
    api<{ status: "DELETED"; campus_deleted: boolean; organization_deleted: boolean }>(`${ACCOUNT}/deletion/${id}/verify`, {
      json: { otp },
    }),
  exportCampus: (id: string) => download(`${ACCOUNT}/deletion/${id}/export`, "campus-export.json"),
  startTransfer: (recipient_admin_id: string, current_password: string) =>
    api<CodeSentLike>(`${ACCOUNT}/ownership-transfer`, { json: { recipient_admin_id, current_password } }),
  resendTransfer: (id: string) => api<CodeSentLike>(`${ACCOUNT}/ownership-transfer/${id}/resend-otp`, { method: "POST" }),
  verifyTransfer: (id: string, otp: string) =>
    api<{ transfer_id: string; accept_by: string }>(`${ACCOUNT}/ownership-transfer/${id}/verify`, { json: { otp } }),
  cancelTransfer: (id: string) => api<void>(`${ACCOUNT}/ownership-transfer/${id}`, { method: "DELETE" }),
  incoming: () => api<IncomingTransfer[]>(`${TRANSFERS}/incoming`),
  history: () => api<OwnershipHistoryEntry[]>(`${TRANSFERS}/history`),
  accept: (id: string) => api<{ status: string; you_are_main_admin: boolean }>(`${TRANSFERS}/${id}/accept`, { method: "POST" }),
  reject: (id: string) => api<{ status: string }>(`${TRANSFERS}/${id}/reject`, { method: "POST" }),
};
