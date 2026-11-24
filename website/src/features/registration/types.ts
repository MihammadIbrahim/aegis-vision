// Organization registration (public; POST /auth/organization-registrations…). Field names are the Core
// API's (organization_registration_dto.py).

/** OrganizationRegistrationRequest. The applicant becomes the organization's Main Admin. */
export interface RegistrationBody {
  organization_name: string;
  /** The first campus's name (decision D-021); required. */
  campus_name: string;
  admin_full_name: string;
  username: string;
  email: string;
  phone_number: string;
  password: string;
}

/** VerificationCodeSentResponse. */
export interface RegistrationCodeSent {
  registration_id: string;
  status: string;
  /** Masked, e.g. ay***@example.com. */
  email: string;
  delivery: string;
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
  registration_expires_at: string;
}

/** RegistrationCompletedResponse. ``organization_id`` is needed to sign in and to request more campuses;
 * ``main_admin.user_id`` is the Main Admin ID. */
export interface RegistrationCompleted {
  status: string;
  organization_id: string;
  organization_name: string;
  official_email: string;
  official_phone_number: string;
  campus_name: string;
  main_admin: {
    user_id: string;
    full_name: string;
    username: string;
    email: string;
    phone_number: string;
    role: string;
    is_main_admin: boolean;
  };
  registered_at: string;
}

// "Already Registered Organization?" campus requests (POST /auth/campus-requests…; campus_request_application_dto.py).

/** CampusRequestApplicationRequest. The organization must match its exact name and Organization ID. */
export interface CampusRequestBody {
  organization_name: string;
  organization_id: string;
  campus_name: string;
  admin_full_name: string;
  username: string;
  email: string;
  phone_number: string;
  password: string;
}

/** CampusRequestCodeSentResponse. */
export interface CampusRequestCodeSent {
  application_id: string;
  status: string;
  /** Masked, e.g. ha***@example.com. */
  email: string;
  delivery: string;
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
  application_expires_at: string;
}

/** CampusRequestVerifiedResponse: the request now waits for the Main Admin's approval. */
export interface CampusRequestVerified {
  status: "PENDING_APPROVAL";
  organization_name: string;
  campus_name: string;
  email: string;
  email_verified_at: string;
  /** The request expires if it is not decided by then (72 hours after verification). */
  decision_deadline: string;
}
