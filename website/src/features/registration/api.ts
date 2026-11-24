import { api } from "../../api/client";
import type {
  CampusRequestBody,
  CampusRequestCodeSent,
  CampusRequestVerified,
  RegistrationBody,
  RegistrationCodeSent,
  RegistrationCompleted,
} from "./types";

const BASE = "/auth/organization-registrations";

export const registration = {
  submit: (body: RegistrationBody) => api<RegistrationCodeSent>(BASE, { json: body }),
  resend: (id: string) => api<RegistrationCodeSent>(`${BASE}/${id}/resend-otp`, { method: "POST" }),
  verify: (id: string, otp: string) => api<RegistrationCompleted>(`${BASE}/${id}/verify`, { json: { otp } }),
};

const CAMPUS = "/auth/campus-requests";

/** "Already Registered Organization?": a new campus under an existing organization. No token is issued. */
export const campusRequests = {
  submit: (body: CampusRequestBody) => api<CampusRequestCodeSent>(CAMPUS, { json: body }),
  resend: (id: string) => api<CampusRequestCodeSent>(`${CAMPUS}/${id}/resend-otp`, { method: "POST" }),
  verify: (id: string, otp: string) => api<CampusRequestVerified>(`${CAMPUS}/${id}/verify`, { json: { otp } }),
};
