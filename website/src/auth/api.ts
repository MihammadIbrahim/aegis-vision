import { api } from "../api/client";
import type { Session } from "../api/types";

// --- Sign-in (Admins and Supervisors use the same endpoints) ---

export interface LoginForm {
  organization_name: string;
  organization_id: string;
  email: string;
  password: string;
}

export const auth = {
  start: (form: LoginForm) =>
    api<{ login_id: string; email: string; otp_expires_in_seconds: number }>("/auth/organization-admin/login", {
      json: form,
    }),
  resend: (loginId: string) => api<unknown>(`/auth/organization-admin/login/${loginId}/resend-otp`, { method: "POST" }),
  verify: (loginId: string, otp: string) =>
    api<{ access_token: string; expires_in: number }>(`/auth/organization-admin/login/${loginId}/verify`, {
      json: { otp },
    }),
  me: () => api<Session>("/auth/me"),
};

// Forgot password for Admins and Supervisors (public; POST /auth/password-reset…, supervisor_dto.py).
// The first step answers the same whether or not an account matches, so it never reveals accounts.

/** PasswordResetRequest: exactly one of organization_id / supervisor_id, plus the email. */
export type PasswordResetBody = { email: string } & ({ organization_id: string } | { supervisor_id: string });

/** PasswordResetStartedResponse (202). */
export interface PasswordResetStarted {
  reset_id: string;
  status: "OTP_REQUIRED";
  message: string;
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
  expires_at: string;
}

export const passwordReset = {
  start: (body: PasswordResetBody) => api<PasswordResetStarted>("/auth/password-reset", { json: body }),
  resend: (id: string) => api<PasswordResetStarted>(`/auth/password-reset/${id}/resend-otp`, { method: "POST" }),
  verify: (id: string, otp: string) =>
    api<{ reset_token: string; expires_in: number }>(`/auth/password-reset/${id}/verify`, { json: { otp } }),
  complete: (id: string, reset_token: string, new_password: string, confirm_new_password: string) =>
    api<{ status: "PASSWORD_CHANGED"; message: string }>(`/auth/password-reset/${id}/complete`, {
      json: { reset_token, new_password, confirm_new_password },
    }),
};
