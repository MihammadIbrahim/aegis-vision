// Every Platform Super Admin call, with the Core API's exact methods, paths, queries and bodies.
import { api, download } from "./client";
import type {
  AiSettings,
  AuditAction,
  AuditPage,
  AuditQuery,
  ChangeAiPermissionBody,
  ChangeStatusBody,
  DeletedOrganizations,
  Health,
  Organization,
  OrganizationAiPermissions,
  OrganizationDetail,
  OrganizationPage,
  OrganizationQuery,
  Overview,
  PlatformAdmin,
  TokenResponse,
  UpdateAiSettingsBody,
  UpdateMainAdminBody,
} from "./types";

const code = (organizationId: string) => encodeURIComponent(organizationId);

export const platform = {
  // Authentication and own account.
  login: (username: string, password: string) =>
    api<TokenResponse>("/platform/auth/login", { json: { username, password } }),
  me: () => api<PlatformAdmin>("/platform/me"),
  changeUsername: (current_password: string, new_username: string) =>
    api<TokenResponse>("/platform/me/username", { json: { current_password, new_username } }),
  changePassword: (current_password: string, new_password: string, confirm_new_password: string) =>
    api<TokenResponse>("/platform/me/password", { json: { current_password, new_password, confirm_new_password } }),
  recoverCredentials: (body: {
    recovery_username: string;
    recovery_password: string;
    new_username: string;
    new_password: string;
    confirm_new_password: string;
  }) => api<TokenResponse>("/platform/recovery/credentials", { json: body }),

  // Overview and organizations.
  overview: () => api<Overview>("/platform/overview"),
  organizations: (query: OrganizationQuery) => api<OrganizationPage>("/platform/organizations", { query: { ...query } }),
  exportOrganizations: (query: OrganizationQuery) =>
    download("/platform/organizations/export", "organizations.csv", { q: query.q, status: query.status, sort: query.sort, order: query.order }),
  organization: (organizationId: string) => api<OrganizationDetail>(`/platform/organizations/${code(organizationId)}`),
  updateMainAdmin: (organizationId: string, body: UpdateMainAdminBody) =>
    api<Organization>(`/platform/organizations/${code(organizationId)}/main-admin`, { method: "PUT", json: body }),
  changeStatus: (organizationId: string, body: ChangeStatusBody) =>
    api<Organization>(`/platform/organizations/${code(organizationId)}/status`, { json: body }),
  aiPermissions: (organizationId: string) =>
    api<OrganizationAiPermissions>(`/platform/organizations/${code(organizationId)}/ai-permissions`),
  changeAiPermission: (organizationId: string, body: ChangeAiPermissionBody) =>
    api<OrganizationAiPermissions>(`/platform/organizations/${code(organizationId)}/ai-permissions`, { method: "PUT", json: body }),
  deletedOrganizations: () => api<DeletedOrganizations>("/platform/deleted-organizations"),

  // AI settings, health, audit log.
  aiSettings: () => api<AiSettings>("/platform/ai-settings"),
  updateAiSettings: (body: UpdateAiSettingsBody) => api<AiSettings>("/platform/ai-settings", { method: "PUT", json: body }),
  health: () => api<Health>("/platform/health"),
  auditLogs: (query: AuditQuery) => api<AuditPage>("/platform/audit-logs", { query: { ...query } }),
  auditActions: () => api<AuditAction[]>("/platform/audit-logs/actions"),
  exportAuditLogs: (query: AuditQuery) =>
    download("/platform/audit-logs/export", "platform-audit-log.csv", {
      organization: query.organization,
      action: query.action,
      outcome: query.outcome,
      date_from: query.date_from,
      date_to: query.date_to,
    }),
};
