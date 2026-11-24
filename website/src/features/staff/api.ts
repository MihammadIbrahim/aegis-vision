import { api } from "../../api/client";
import type { LocationSelection } from "../locations";
import type {
  AuthorizationVerified,
  CodeSent,
  CreateStaffBody,
  GuardCreated,
  SetGuardPasswordBody,
  StaffMember,
  StaffQuery,
  StaffRole,
  StaffStatusBody,
  SupervisorCreated,
  UpdateGuardBody,
} from "./types";

export const staff = {
  list: (query: StaffQuery) => api<StaffMember[]>("/staff", { query: { ...query } }),
  get: (id: string) => api<StaffMember>(`/staff/${id}`),
  updateGuard: (id: string, body: UpdateGuardBody) => api<StaffMember>(`/staff/guards/${id}`, { method: "PUT", json: body }),
  setGuardPassword: (id: string, body: SetGuardPasswordBody) =>
    api<void>(`/staff/guards/${id}/password`, { method: "PUT", json: body }),
  deactivate: (id: string, body: StaffStatusBody) => api<StaffMember>(`/staff/${id}/deactivate`, { json: body }),
  reactivate: (id: string, body: StaffStatusBody) => api<StaffMember>(`/staff/${id}/reactivate`, { json: body }),
  /** PUT /locations/guard-assignments/{guard_id}: the Guard becomes Stationed (Guard ID, not the internal ID). */
  assignGuard: (guardId: string, body: LocationSelection) =>
    api<unknown>(`/locations/guard-assignments/${encodeURIComponent(guardId)}`, { method: "PUT", json: body }),
  /** DELETE /locations/guard-assignments/{guard_id}: the Guard becomes Standby. Both calls end the
   * Guard's open sessions (decision D-018). */
  unassignGuard: (guardId: string) =>
    api<void>(`/locations/guard-assignments/${encodeURIComponent(guardId)}`, { method: "DELETE" }),
};

/** Where each kind of account is created (Admins only). */
export const CREATION_PREFIX: Record<StaffRole, string> = {
  GUARD: "/guards",
  SUPERVISOR: "/organization-admin/supervisors",
};

/** The four-step creation flow: own password → own code → the form → the new user's code. */
export function creation(role: StaffRole) {
  const p = CREATION_PREFIX[role];
  return {
    authorize: (current_password: string) => api<CodeSent>(`${p}/authorization`, { json: { current_password } }),
    resendAuthorization: (id: string) => api<CodeSent>(`${p}/authorization/${id}/resend-otp`, { method: "POST" }),
    verifyAuthorization: (id: string, otp: string) =>
      api<AuthorizationVerified>(`${p}/authorization/${id}/verify`, { json: { otp } }),
    submit: (body: CreateStaffBody) => api<CodeSent>(p, { json: body }),
    resendCreation: (id: string) => api<CodeSent>(`${p}/creations/${id}/resend-otp`, { method: "POST" }),
    verifyCreation: (id: string, otp: string) =>
      api<GuardCreated | SupervisorCreated>(`${p}/creations/${id}/verify`, { json: { otp } }),
  };
}
