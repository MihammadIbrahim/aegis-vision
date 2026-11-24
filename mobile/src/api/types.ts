/**
 * Core API contract used by the Guard app. Field names and enum values are exactly those of the backend Pydantic
 * models (backend/services/core_api/src/application/dtos/*). Nothing here sends organization_id or campus_id: the
 * server takes the scope from the signed-in Guard's token.
 */

// --- Shared enums --------------------------------------------------------------------------------------------------

export type OperationalMode = "STANDBY" | "STATIONED";
export type GuardDashboardKind = "STANDBY_EMERGENCY" | "STATIONED_MONITORING";
export type AlertType = "WEAPON" | "FIGHT_VIOLENCE" | "RESTRICTED_AREA";
export type AlertPriority = "HIGH" | "STANDARD";
export type AlertStatus = "NEW" | "ACKNOWLEDGED" | "RESPONDING" | "ESCALATED" | "RESOLVED" | "DISMISSED";
export type RecipientState = "NOTIFIED" | "ACKNOWLEDGED" | "RESPONDING";
export type RecipientReason = "LOCATION" | "ESCALATION" | "AUTOMATIC" | "ASSISTANCE";
export type Assessment = "UNDER_CONTROL" | "ADDITIONAL_GUARDS_REQUIRED";
export type GuardAction = "ACKNOWLEDGE" | "RESPONDING" | Assessment;
export type ActionOutcome = "DONE" | "UNCHANGED" | "REPLAYED";
export type StaffRole = "ADMIN" | "SUPERVISOR" | "GUARD";
export type IncidentStatus = "OPEN" | "UNDER_INVESTIGATION" | "RESPONDING" | "RESOLVED" | "DISMISSED";
export type CallScope = "ALERT" | "INCIDENT";
export type CallStatus = "ACTIVE" | "ENDED";
export type ParticipantState = "INVITED" | "JOINED" | "DECLINED" | "LEFT" | "MISSED";
export type EndReason = "ENDED_BY_USER" | "EVERYONE_LEFT" | "TARGET_CLOSED";

// --- Errors ------------------------------------------------------------------------------------------------------------

/** Error body: {"detail": string | validation list, "code"?: string, "retry_after_seconds"?: number}. */
export interface ApiErrorBody {
  detail?: string | { loc: (string | number)[]; msg: string; type: string }[];
  code?: string;
  retry_after_seconds?: number;
  errors?: Record<string, string>;
}

// --- Sign-in (POST /auth/guard/login, /verify, /resend-otp) ---------------------------------------------------------

export interface GuardLoginRequest {
  operational_mode: OperationalMode;
  guard_id: string;
  email: string;
  password: string;
}

export interface LoginCodeSentResponse {
  login_id: string;
  status: "OTP_REQUIRED";
  email: string; // masked, e.g. gu***@company.com
  delivery: string; // "EMAIL" or "DEV_OUTBOX"
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
  login_expires_at: string;
}

export interface GuardVerifyLoginRequest {
  otp: string;
  operational_mode: OperationalMode;
}

export interface GuardTokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  operational_mode: OperationalMode;
  dashboard: GuardDashboardKind;
}

// --- Forgot password (POST /auth/password-reset …) -------------------------------------------------------------------

export interface GuardPasswordResetRequest {
  guard_id: string;
  email: string;
  operational_mode: OperationalMode;
}

export interface PasswordResetStartedResponse {
  reset_id: string;
  status: "OTP_REQUIRED";
  message: string;
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
  expires_at: string;
}

export interface ResetTokenResponse {
  reset_token: string;
  expires_in: number;
}

export interface CompletePasswordResetRequest {
  reset_token: string;
  new_password: string;
  confirm_new_password: string;
}

export interface PasswordChangedResponse {
  status: "PASSWORD_CHANGED";
  message: string;
}

// --- Dashboard, location, profile -----------------------------------------------------------------------------------

export interface GuardDashboardResponse {
  organization: { organization_id: string; name: string };
  campus: { name: string };
  guard: {
    guard_id: string | null;
    full_name: string | null;
    username: string | null;
    email: string | null;
    phone_number: string | null;
    role: string | null;
  };
  not_available_yet: string[];
  operational_mode: OperationalMode;
  dashboard: GuardDashboardKind;
}

export interface OwnGuardLocationResponse {
  assigned: boolean;
  building_name?: string | null;
  building_kind?: "INDOOR" | "OUTDOOR_PERIMETER" | null;
  floor_name?: string | null;
  floor_level?: number | null;
  area_name?: string | null;
  boundary_direction?: "NORTH" | "SOUTH" | "EAST" | "WEST" | "CUSTOM" | null;
  boundary_name?: string | null;
  boundary_side?: "INSIDE" | "OUTSIDE" | null;
}

export interface GuardProfileResponse {
  organization_name: string;
  organization_id: string;
  guard_id: string;
  full_name: string;
  username: string | null;
  email: string;
  phone_number: string | null;
  editable_fields: string[];
  read_only_fields: string[];
  campus_name: string | null;
  status: "ACTIVE" | "LEFT" | null;
  operational_mode: OperationalMode | null;
}

export interface GuardProfileChangeRequest {
  current_password: string;
  email?: string | null;
  phone_number?: string | null;
  new_password?: string | null;
  confirm_new_password?: string | null;
}

export interface ProfileChangeStartedResponse {
  change_id: string;
  status: "OTP_REQUIRED";
  email: string; // masked registered email
  new_email: string | null; // masked new email (a second code was sent there)
  delivery: string;
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
  expires_at: string;
}

export interface VerifyProfileChangeRequest {
  otp: string;
  new_email_otp?: string | null;
}

export interface GuardProfileChangedResponse {
  status: "SAVED";
  profile: GuardProfileResponse;
  signed_out: boolean;
}

// --- Alerts ------------------------------------------------------------------------------------------------------------

export interface RecipientResponse {
  guard_id: string;
  full_name: string;
  reason: RecipientReason;
  state: RecipientState;
  assessment: Assessment | null;
  acknowledged_at: string | null;
  responding_at: string | null;
}

export interface AlertLocation {
  building_id: string | null;
  building_name: string | null;
  building_kind: string | null;
  floor_id: string | null;
  floor_name: string | null;
  area_id: string | null;
  area_name: string | null;
}

export interface AlertResponse {
  id: string;
  alert_type: AlertType;
  priority: AlertPriority;
  status: AlertStatus;
  camera_id: string;
  camera_name: string;
  location: AlertLocation;
  confidence: string | null; // Decimal as a string, e.g. "0.9100"
  detected_at: string;
  last_detected_at: string;
  detection_count: number;
  close_note: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  recipients: RecipientResponse[]; // a Guard sees only their own entry
  handled_by: string | null;
  handled_by_name: string | null;
  handled_at: string | null;
  recipient_count: number;
  acknowledged_count: number;
  responding_count: number;
  has_snapshot: boolean;
}

export interface AlertEventResponse {
  event_type: string;
  actor_role: string;
  actor_name: string | null;
  from_status: string | null;
  to_status: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface ActionResponse {
  alert_id: string;
  outcome: ActionOutcome;
  status: AlertStatus;
}

export interface ResponderResponse {
  full_name: string;
  state: "ACKNOWLEDGED" | "RESPONDING";
  assessment: Assessment | null;
  reason: RecipientReason;
  acknowledged_at: string | null;
  responding_at: string | null;
  is_me: boolean;
}

export interface AlertMessageResponse {
  id: string;
  sender_role: StaffRole;
  sender_name: string;
  body: string;
  created_at: string;
}

// --- Incidents (case chat) --------------------------------------------------------------------------------------------

export interface IncidentResponse {
  id: string;
  reference: string; // INC-000001
  title: string;
  description: string;
  status: IncidentStatus;
  priority: AlertPriority;
  occurred_at: string;
  camera_id: string | null;
  camera_name: string | null;
  building_id: string | null;
  floor_id: string | null;
  area_id: string | null;
  location: string | null;
  assigned_to: string | null;
  assigned_name: string | null;
  created_by: string | null;
  created_by_name: string;
  resolution_note: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  alert_count: number;
  report_count: number;
  evidence_count: number;
}

export interface IncidentMessageResponse {
  id: string;
  sender_user_id: string | null;
  sender_name: string;
  sender_role: StaffRole;
  body: string;
  created_at: string;
}

// --- Manual reports and offline sync --------------------------------------------------------------------------------

export interface ManualReportRequest {
  client_report_id: string;
  report_type: string; // 2–60 characters
  description: string;
  building_id: string;
  floor_id?: string | null;
  area_id?: string | null;
  occurred_at?: string | null;
  photo_base64?: string | null; // JPEG or PNG
}

export interface ManualReportResponse {
  id: string;
  guard_id: string;
  guard_name: string;
  report_type: string;
  description: string;
  building_id: string | null;
  building_name: string;
  floor_id: string | null;
  floor_name: string | null;
  area_id: string | null;
  area_name: string | null;
  has_photo: boolean;
  occurred_at: string | null;
  status: "SUBMITTED" | "REVIEWED";
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface SubmittedReportResponse {
  report: ManualReportResponse;
  created: boolean;
}

export interface GuardLocationOption {
  building_id: string;
  building_name: string;
  building_kind: string;
  floor_id: string | null;
  floor_name: string | null;
  floor_level: number | null;
  area_id: string | null;
  area_name: string | null;
}

export type SyncItem =
  | { kind: "ALERT_ACTION"; client_action_id: string; alert_id: string; action: GuardAction }
  | { kind: "MANUAL_REPORT"; report: ManualReportRequest }
  | { kind: "ALERT_MESSAGE"; client_message_id: string; alert_id: string; body: string };

export interface SyncItemResult {
  index: number;
  kind: string;
  ok: boolean;
  http_status: number;
  code: string | null;
  detail: string | null;
  outcome: string | null;
  target_id: string | null;
}

// --- Notifications ----------------------------------------------------------------------------------------------------

export interface NotificationResponse {
  id: string;
  kind: string;
  title: string;
  alert_id: string | null;
  manual_report_id: string | null;
  subject_user_id: string | null;
  camera_id: string | null;
  incident_id: string | null;
  created_at: string;
  read_at: string | null;
}

// --- Voice calls (D-025) ------------------------------------------------------------------------------------------------

export interface CallParticipantResponse {
  user_id: string;
  full_name: string;
  role: StaffRole;
  state: ParticipantState;
  invited_at: string;
  joined_at: string | null;
  left_at: string | null;
  is_me: boolean;
}

export interface CallSessionResponse {
  id: string;
  scope: CallScope;
  alert_id: string | null;
  incident_id: string | null;
  status: CallStatus;
  media: "AUDIO";
  started_by: string | null;
  started_by_name: string | null;
  started_by_role: StaffRole;
  started_at: string;
  ended_at: string | null;
  end_reason: EndReason | null;
  joined_count: number;
  my_state: ParticipantState | null;
  can_end: boolean;
  participants: CallParticipantResponse[];
  /** D-026: what the call is about ("Weapon alert · Gate Camera" / "INC-000007 · Case title") and where. */
  target_title?: string;
  target_location?: string | null;
}

export interface VoiceConnectionResponse {
  provider: "livekit";
  url: string;
  room: string;
  identity: string;
  token: string;
  expires_in: number;
}

export interface CallJoinResponse {
  outcome: "STARTED" | "JOINED";
  call: CallSessionResponse;
  connection: VoiceConnectionResponse;
}

export interface ActiveCallResponse {
  voice_calls_available: boolean;
  provider: "none" | "livekit";
  call: CallSessionResponse | null;
}

export interface LeaveCallResponse {
  call: CallSessionResponse;
  call_ended: boolean;
}

// --- Device tokens and real-time status (D-025) ------------------------------------------------------------------------

export interface DeviceTokenRequest {
  token: string;
  platform: "ANDROID" | "IOS";
  provider: "FCM" | "APNS";
  app_version?: string | null;
}

export interface DeviceTokenResponse {
  id: string;
  platform: "ANDROID" | "IOS";
  provider: "FCM" | "APNS";
  push_delivery_mode: "none" | "fcm";
}

export interface RealtimeStatusResponse {
  websocket_path: string;
  heartbeat_seconds: number;
  auth_timeout_seconds: number;
  voice_call_provider: "none" | "livekit";
  voice_calls_available: boolean;
  push_delivery_mode: "none" | "fcm";
}

/** Messages on the WebSocket (server → client). */
export type RealtimeServerMessage =
  | { type: "ready"; user_id: string; role: StaffRole; heartbeat_seconds: number; token_expires_at: string }
  | { type: "pong" }
  | {
      type: "event";
      event: RealtimeEventName;
      at: string;
      alert_id?: string;
      incident_id?: string;
      call_id?: string;
      state?: string;
      kind?: string;
    }
  | { type: "session_ended"; code: string; detail: string }
  | { type: "error"; code: string; detail: string };

export type RealtimeEventName =
  | "alert.assigned"
  | "alert.updated"
  | "alert.message"
  | "incident.message"
  | "incident.updated"
  | "incident.invited"
  | "incident.removed"
  | "notification"
  | "call.invited"
  | "call.updated";
