import { api } from "../../api/client";
import type { AuditEntry } from "../personnel";
import type { AccessCheck, Grant, RestrictedArea, Schedule } from "./types";

export interface GrantTerms {
  valid_from?: string | null;
  valid_until?: string | null;
  schedule?: Schedule | null;
  note?: string | null;
}

export const access = {
  areas: (includeUnrestricted = false) =>
    api<RestrictedArea[]>("/restricted-access/areas", { query: { include_unrestricted: includeUnrestricted } }),
  mark: (areaId: string, note: string | null) =>
    api<RestrictedArea>(`/restricted-access/areas/${areaId}`, { method: "PUT", json: { access_note: note } }),
  clear: (areaId: string) => api<void>(`/restricted-access/areas/${areaId}`, { method: "DELETE" }),
  areaGrants: (areaId: string, includeRevoked = false) =>
    api<Grant[]>(`/restricted-access/areas/${areaId}/grants`, { query: { include_revoked: includeRevoked } }),
  personGrants: (personnelId: string, includeRevoked = false) =>
    api<Grant[]>(`/restricted-access/personnel/${personnelId}/grants`, { query: { include_revoked: includeRevoked } }),
  grant: (personnelId: string, areaIds: string[], terms: GrantTerms) =>
    api<Grant[]>("/restricted-access/grants", { json: { personnel_id: personnelId, area_ids: areaIds, ...terms } }),
  update: (grantId: string, terms: GrantTerms) =>
    api<Grant>(`/restricted-access/grants/${grantId}`, { method: "PUT", json: terms }),
  revoke: (grantId: string, reason: string | null) =>
    api<Grant>(`/restricted-access/grants/${grantId}/revoke`, { json: { reason } }),
  history: (grantId: string) =>
    api<{ grant: Grant; history: AuditEntry[] }>(`/restricted-access/grants/${grantId}/history`),
  check: (personnelId: string, areaId: string, at?: string) =>
    api<AccessCheck>("/restricted-access/check", { query: { personnel_id: personnelId, area_id: areaId, at } }),
};
