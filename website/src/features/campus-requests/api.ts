import { api } from "../../api/client";
import type { CodeSentLike } from "../account/components/StepUpDialog";

// Campus/Branch Approval Requests: the Main Admin's side (Org Admin page 15; decisions D-020, H, I, D-024).

export type CampusRequestStatus = "PENDING" | "APPROVED" | "REJECTED";

export interface CampusRequest {
  id: string;
  /** Null once a rejection erased the applicant's details (decision I). */
  proposed_campus_name: string | null;
  applicant_name: string | null;
  applicant_email: string | null;
  email_verified: boolean;
  status: CampusRequestStatus;
  submitted_at: string;
  reviewed_at: string | null;
  applicant_username: string | null;
  applicant_phone: string | null;
  email_verified_at: string | null;
  decision_deadline: string | null;
}

export interface CampusRequestRecord extends CampusRequest {
  details_erased_at: string | null;
  reviewed_by_name: string | null;
}

export interface CampusRequestHistory {
  items: CampusRequestRecord[];
  total: number;
  limit: number;
  offset: number;
  counts: Record<CampusRequestStatus, number>;
}

const BASE = "/campus-requests";

export const campusRequests = {
  pending: () => api<CampusRequest[]>(BASE),
  history: (query: { status?: CampusRequestStatus | ""; q?: string; limit: number; offset: number }) =>
    api<CampusRequestHistory>(`${BASE}/history`, { query }),
  startAuthorization: (current_password: string) =>
    api<CodeSentLike>(`${BASE}/authorization`, { json: { current_password } }),
  resendAuthorization: (id: string) => api<CodeSentLike>(`${BASE}/authorization/${id}/resend-otp`, { method: "POST" }),
  verifyAuthorization: (id: string, otp: string) =>
    api<{ authorization_id: string; form_expires_at: string }>(`${BASE}/authorization/${id}/verify`, { json: { otp } }),
  approve: (id: string, authorization_id: string) =>
    api<CampusRequest>(`${BASE}/${id}/approve`, { json: { authorization_id } }),
  reject: (id: string, current_password: string) =>
    api<CampusRequest>(`${BASE}/${id}/reject`, { json: { current_password } }),
};
