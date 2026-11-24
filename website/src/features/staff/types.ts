// Staff Management (decision D-017). Field names are the Core API's: staff_dto.py (GET/PUT/POST
// /staff…), supervisor_dto.py / guard_dto.py (creation), location_dto.py (Guard locations).
import type { LocationPath, LocationSelection } from "../locations";

export type StaffRole = "GUARD" | "SUPERVISOR";
export type StaffStatus = "ACTIVE" | "LEFT";
/** Guards only: STANDBY without a location (campus-wide dispatch), STATIONED with one. */
export type GuardCategory = "STANDBY" | "STATIONED";

/** StaffMemberResponse. ``id`` is the internal user ID used in /staff URLs; ``display_id`` is the
 * Guard ID or Supervisor ID people see (also used by /locations/guard-assignments/{guard_id}). */
export interface StaffMember {
  id: string;
  display_id: string;
  role: StaffRole;
  status: StaffStatus;
  full_name: string;
  username: string | null;
  email: string;
  phone_number: string | null;
  created_at: string;
  category: GuardCategory | null;
  location: LocationPath | null;
}

/** GET /staff query. Supervisors may list GUARD only. */
export interface StaffQuery {
  role: StaffRole;
  status?: StaffStatus;
  category?: GuardCategory;
  q?: string;
  limit: number;
  offset: number;
}

/** PUT /staff/guards/{id}. Email changes stay with the Guard's own verified flow. */
export interface UpdateGuardBody {
  full_name: string;
  username: string;
  phone_number: string;
  current_password: string;
}

/** PUT /staff/guards/{id}/password. */
export interface SetGuardPasswordBody {
  new_password: string;
  current_password: string;
}

/** POST /staff/{id}/deactivate and /reactivate (Admins). */
export interface StaffStatusBody {
  current_password: string;
}

// --- Creation (Admins only): /guards… and /organization-admin/supervisors… ------------------------

/** CodeSentResponse: a code was emailed (to the Admin for the confirmation, to the new user for the form). */
export interface CodeSent {
  id: string;
  status: "OTP_REQUIRED";
  /** Masked, e.g. ay***@example.com. */
  email: string;
  delivery: string;
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
  expires_at: string;
}

/** AuthorizationVerifiedResponse. */
export interface AuthorizationVerified {
  authorization_id: string;
  status: "VERIFIED";
  form_expires_at: string;
}

/** CreateSupervisorRequest (also the Guard form). The password is set by the Admin. */
export interface CreateStaffBody {
  authorization_id: string;
  full_name: string;
  username: string;
  email: string;
  phone_number: string;
  password: string;
}

interface CreatedBase {
  full_name: string;
  username: string;
  email: string;
  phone_number: string;
  organization_id: string;
  campus_name: string;
  created_at: string;
}

export interface GuardCreated extends CreatedBase {
  guard_id: string;
  role: "GUARD";
}

export interface SupervisorCreated extends CreatedBase {
  supervisor_id: string;
  role: "SUPERVISOR";
}

export type { LocationSelection };
