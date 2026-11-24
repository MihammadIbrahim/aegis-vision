// Shapes of the Core API's Platform Super Admin routes, field for field (decision D-019). Source:
// backend/services/core_api/src/application/dtos/platform_admin_dto.py and platform_management_dto.py,
// and the SRS section of FYP_Final_Super_Admin_Dashboard_Pages_and_Their_Each_Page_Features.txt.

// --- Authentication and profile -------------------------------------------------------------------

/** TokenResponse: POST /platform/auth/login, /me/username, /me/password, /recovery/credentials. */
export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  /** Seconds. */
  expires_in: number;
}

/** PlatformAdminResponse: GET /platform/me. */
export interface PlatformAdmin {
  id: string;
  username: string;
  role: "PLATFORM_SUPER_ADMIN";
  created_at: string;
  last_login_at: string | null;
}

/** How the current session was opened (from the token's "lm" claim; display only). */
export type LoginMethod = "NORMAL" | "RECOVERY";

/** DeletedOrganizationsResponse. */
export interface DeletedOrganizations {
  organization_names: string[];
}

// --- Organizations ----------------------------------------------------------------------------------

export type OrganizationStatus = "ACTIVE" | "INACTIVE" | "SUSPENDED";
export type OrganizationSort = "name" | "created_at" | "camera_count";
export type SortOrder = "asc" | "desc";

export interface MainAdmin {
  full_name: string;
  username: string | null;
  email: string;
  phone_number: string | null;
}

/** OrganizationResponse. ``organization_id`` is the public Organization ID (6 capital letters + 11 digits;
 * IDs issued before migration 0005 are ORG- + 11 digits). */
export interface Organization {
  organization_id: string;
  name: string;
  official_email: string | null;
  official_phone_number: string | null;
  status: OrganizationStatus;
  created_at: string;
  main_admin: MainAdmin | null;
  campus_count: number;
  camera_count: number;
}

export interface OrganizationPage {
  items: Organization[];
  total: number;
  limit: number;
  offset: number;
}

/** GET /platform/organizations and /export query. */
export interface OrganizationQuery {
  q?: string;
  status?: OrganizationStatus;
  sort?: OrganizationSort;
  order?: SortOrder;
  limit?: number;
  offset?: number;
}

/** CampusResponse. Staff counts are active accounts. */
export interface Campus {
  name: string;
  status: string;
  created_at: string;
  building_count: number;
  perimeter_count: number;
  floor_count: number;
  area_count: number;
  camera_count: number;
  admin_count: number;
  supervisor_count: number;
  guard_count: number;
}

/** OrganizationDetailResponse. ``registered_address`` is always null (not collected). */
export interface OrganizationDetail extends Organization {
  registered_address: null;
  campuses: Campus[];
  /** AI feature access for this organization (decision D-028). */
  ai_permissions?: OrganizationAiPermission[];
}

export type AiPermissionStatus = "ALLOWED" | "BLOCKED" | "DISABLED_GLOBALLY";

/** OrganizationAiPermissionResponse: one vision module for one organization. */
export interface OrganizationAiPermission {
  module_key: string;
  feature: string;
  label: string;
  /** One of the four modules the Super Admin blocks per organization. */
  core: boolean;
  blocked: boolean;
  platform_enabled: boolean;
  status: AiPermissionStatus;
}

/** GET / PUT /platform/organizations/{organization_id}/ai-permissions. */
export interface OrganizationAiPermissions {
  organization_id: string;
  ai_permissions: OrganizationAiPermission[];
}

/** ChangeOrganizationAiPermissionRequest: one feature per click, with the password (SA-SEC-4). */
export interface ChangeAiPermissionBody {
  module_key: string;
  blocked: boolean;
  current_password: string;
}

/** UpdateMainAdminRequest. */
export interface UpdateMainAdminBody {
  full_name: string;
  email: string;
  phone_number: string;
  current_password: string;
}

/** ChangeOrganizationStatusRequest. */
export interface ChangeStatusBody {
  status: OrganizationStatus;
  reason?: string | null;
  current_password: string;
}

// --- AI settings ---------------------------------------------------------------------------------------

export const AI_MODULE_KEYS = [
  "human_detection_tracking_enabled",
  "person_reid_enabled",
  "weapon_detection_enabled",
  "fight_violence_detection_enabled",
  "restricted_area_face_recognition_enabled",
  "rag_assistant_enabled",
  "agentic_investigation_enabled",
] as const;
export type AiModuleKey = (typeof AI_MODULE_KEYS)[number];

/** The twelve values of PUT /platform/ai-settings (a full replacement). */
export interface AiValues {
  human_detection_tracking_enabled: boolean;
  person_reid_enabled: boolean;
  weapon_detection_enabled: boolean;
  fight_violence_detection_enabled: boolean;
  restricted_area_face_recognition_enabled: boolean;
  rag_assistant_enabled: boolean;
  agentic_investigation_enabled: boolean;
  /** Decimal 0.05–0.99 with two decimals; the API sends it as a string, e.g. "0.50". */
  default_confidence_threshold: string;
  frame_sample_fps: number;
  detection_cooldown_seconds: number;
  max_task_retries: number;
  task_timeout_seconds: number;
}

export interface AiModuleStatus {
  key: AiModuleKey;
  enabled: boolean;
  /** False: the module is not built yet, so enabling it has no effect. */
  implemented: boolean;
}

/** AiSettingsResponse. */
export interface AiSettings extends AiValues {
  modules: AiModuleStatus[];
  updated_at: string;
  updated_by_username: string | null;
}

export type UpdateAiSettingsBody = AiValues & { current_password: string };

// --- Health ----------------------------------------------------------------------------------------------

export type ServiceStatus = "OPERATIONAL" | "DEGRADED" | "DOWN" | "NOT_DEPLOYED";

export interface ServiceHealth {
  key: string;
  name: string;
  status: ServiceStatus;
  latency_ms: number | null;
  detail: string;
  metrics: Record<string, unknown>;
}

export interface StorageHealth {
  path: string;
  accessible: boolean;
  writable: boolean;
  total_bytes: number | null;
  used_bytes: number | null;
  free_bytes: number | null;
  used_percent: number | null;
  level: "OK" | "WARNING" | "CRITICAL" | "UNAVAILABLE";
  warning_percent: number;
  critical_percent: number;
}

/** HealthResponse: GET /platform/health. */
export interface Health {
  checked_at: string;
  overall_status: "OPERATIONAL" | "DEGRADED" | "DOWN";
  summary: string;
  services: ServiceHealth[];
  storage: StorageHealth;
  problems: string[];
}

// --- Audit log -------------------------------------------------------------------------------------------

export type ActorType = "SUPER_ADMIN" | "ORGANIZATION_ADMIN" | "SYSTEM";

/** AuditLogEntryResponse. ``details`` never holds passwords, codes or tokens. */
export interface AuditEntry {
  id: string;
  occurred_at: string;
  actor_type: ActorType;
  actor_username: string | null;
  action: string;
  outcome: "SUCCESS" | "FAILURE";
  organization_id: string | null;
  organization_name: string | null;
  ip_address: string | null;
  user_agent: string | null;
  details: Record<string, unknown>;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  limit: number;
  offset: number;
}

/** GET /platform/audit-logs and /export query. Dates are YYYY-MM-DD (UTC); date_to includes that day. */
export interface AuditQuery {
  organization?: string;
  action?: string;
  outcome?: "SUCCESS" | "FAILURE";
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export interface AuditAction {
  action: string;
  count: number;
}

// --- Overview --------------------------------------------------------------------------------------------

export interface PlatformCounts {
  organizations_total: number;
  organizations_active: number;
  organizations_inactive: number;
  organizations_suspended: number;
  deleted_organizations_total: number;
  campuses_total: number;
  campuses_active: number;
  cameras_total: number;
  admins_active: number;
  supervisors_active: number;
  guards_active: number;
}

/** OverviewResponse: GET /platform/overview. */
export interface Overview {
  counts: PlatformCounts;
  health: { overall_status: Health["overall_status"]; summary: string };
  ai_modules: AiModuleStatus[];
  recent_activity: AuditEntry[];
}
