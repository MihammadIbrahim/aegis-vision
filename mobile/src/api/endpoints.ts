/**
 * Every Core API route the Guard app uses (all under /api/v1). Kept in one place so the contract in
 * FYP_Final_Guards_Mobile_App_Pages_and_Their_Each_Page_Features.txt (SRS section, decision D-025) can be checked
 * against it line by line.
 */
import { api } from "./client";
import type {
  ActionResponse,
  ActiveCallResponse,
  AlertEventResponse,
  AlertMessageResponse,
  AlertResponse,
  AlertStatus,
  Assessment,
  CallJoinResponse,
  CallSessionResponse,
  CompletePasswordResetRequest,
  DeviceTokenRequest,
  DeviceTokenResponse,
  GuardDashboardResponse,
  GuardLocationOption,
  GuardLoginRequest,
  GuardPasswordResetRequest,
  GuardProfileChangedResponse,
  GuardProfileChangeRequest,
  GuardProfileResponse,
  GuardTokenResponse,
  GuardVerifyLoginRequest,
  IncidentMessageResponse,
  IncidentResponse,
  LeaveCallResponse,
  LoginCodeSentResponse,
  ManualReportRequest,
  ManualReportResponse,
  NotificationResponse,
  OwnGuardLocationResponse,
  PasswordChangedResponse,
  PasswordResetStartedResponse,
  ProfileChangeStartedResponse,
  RealtimeStatusResponse,
  ResetTokenResponse,
  ResponderResponse,
  SubmittedReportResponse,
  SyncItem,
  SyncItemResult,
  VerifyProfileChangeRequest,
} from "./types";

const PUBLIC = { auth: false } as const;
export const REPORT_PAGE = 50;

// --- Screens 1–2: sign-in and email code ---------------------------------------------------------------------------

export const authApi = {
  login: (body: GuardLoginRequest) => api.post<LoginCodeSentResponse>("/auth/guard/login", body, PUBLIC),
  resendLoginCode: (loginId: string) =>
    api.post<LoginCodeSentResponse>(`/auth/guard/login/${loginId}/resend-otp`, undefined, PUBLIC),
  verifyLogin: (loginId: string, body: GuardVerifyLoginRequest) =>
    api.post<GuardTokenResponse>(`/auth/guard/login/${loginId}/verify`, body, PUBLIC),

  // --- Screens 3–4: forgot and reset password ---
  requestReset: (body: GuardPasswordResetRequest) =>
    api.post<PasswordResetStartedResponse>("/auth/password-reset", body, PUBLIC),
  resendResetCode: (resetId: string) =>
    api.post<PasswordResetStartedResponse>(`/auth/password-reset/${resetId}/resend-otp`, undefined, PUBLIC),
  verifyResetCode: (resetId: string, otp: string) =>
    api.post<ResetTokenResponse>(`/auth/password-reset/${resetId}/verify`, { otp }, PUBLIC),
  completeReset: (resetId: string, body: CompletePasswordResetRequest) =>
    api.post<PasswordChangedResponse>(`/auth/password-reset/${resetId}/complete`, body, PUBLIC),
};

// --- Screen 5 and 10: dashboard, assignment, profile ----------------------------------------------------------------

export const guardApi = {
  dashboard: () => api.get<GuardDashboardResponse>("/guard/dashboard"),
  location: () => api.get<OwnGuardLocationResponse>("/guard/location"),
  profile: () => api.get<GuardProfileResponse>("/guard/profile"),
  startProfileChange: (body: GuardProfileChangeRequest) =>
    api.post<ProfileChangeStartedResponse>("/guard/profile/changes", body),
  resendProfileCodes: (changeId: string) =>
    api.post<ProfileChangeStartedResponse>(`/guard/profile/changes/${changeId}/resend-otp`),
  verifyProfileChange: (changeId: string, body: VerifyProfileChangeRequest) =>
    api.post<GuardProfileChangedResponse>(`/guard/profile/changes/${changeId}/verify`, body),
  cancelProfileChange: (changeId: string) => api.delete<void>(`/guard/profile/changes/${changeId}`),
  registerDevice: (body: DeviceTokenRequest) => api.post<DeviceTokenResponse>("/guard/device-tokens", body),
  forgetDevice: (token: string) => api.delete<void>(`/guard/device-tokens/${encodeURIComponent(token)}`),
  notifications: (unreadOnly = false) =>
    api.get<NotificationResponse[]>(`/notifications?limit=50${unreadOnly ? "&unread_only=true" : ""}`),
  readNotification: (id: string) => api.post<void>(`/notifications/${id}/read`),
  readAllNotifications: () => api.post<{ marked_read: number }>("/notifications/read-all"),
  realtimeStatus: () => api.get<RealtimeStatusResponse>("/realtime/status"),
};

// --- Screens 5–6: alerts and Guard actions ---------------------------------------------------------------------------

export const alertsApi = {
  list: (status?: AlertStatus) => api.get<AlertResponse[]>(`/alerts?limit=200${status ? `&status=${status}` : ""}`),
  get: (alertId: string) => api.get<AlertResponse>(`/alerts/${alertId}`),
  history: (alertId: string) => api.get<AlertEventResponse[]>(`/alerts/${alertId}/history`),
  responders: (alertId: string) => api.get<ResponderResponse[]>(`/alerts/${alertId}/responders`),
  acknowledge: (alertId: string, clientActionId: string) =>
    api.post<ActionResponse>(`/alerts/${alertId}/acknowledge`, { client_action_id: clientActionId }),
  responding: (alertId: string, clientActionId: string) =>
    api.post<ActionResponse>(`/alerts/${alertId}/responding`, { client_action_id: clientActionId }),
  assessment: (alertId: string, assessment: Assessment, clientActionId: string) =>
    api.post<ActionResponse>(`/alerts/${alertId}/assessment`, { assessment, client_action_id: clientActionId }),
  messages: (alertId: string) => api.get<AlertMessageResponse[]>(`/alerts/${alertId}/messages`),
  postMessage: (alertId: string, body: string, clientMessageId: string) =>
    api.post<AlertMessageResponse[]>(`/alerts/${alertId}/messages`, { body, client_message_id: clientMessageId }),
};

// --- Screen 7: case chat ---------------------------------------------------------------------------------------------

export const incidentsApi = {
  mine: () => api.get<IncidentResponse[]>("/guard/incidents"),
  messages: (incidentId: string) => api.get<IncidentMessageResponse[]>(`/incidents/${incidentId}/messages`),
  postMessage: (incidentId: string, body: string) =>
    api.post<IncidentMessageResponse>(`/incidents/${incidentId}/messages`, { body }),
};

// --- Screens 8–9: manual reports and the offline queue ------------------------------------------------------------------

export const reportsApi = {
  locations: () => api.get<GuardLocationOption[]>("/guard/locations"),
  submit: (body: ManualReportRequest) => api.post<SubmittedReportResponse>("/guard/manual-reports", body),
  mine: (offset = 0, limit = REPORT_PAGE) =>
    api.get<ManualReportResponse[]>(`/guard/manual-reports?limit=${limit}&offset=${offset}`),
  sync: (items: SyncItem[]) => api.post<SyncItemResult[]>("/guard/sync", { items }),
};

// --- In-app voice calls (D-025) ------------------------------------------------------------------------------------------

export type CallTargetRef = { scope: "ALERT"; id: string } | { scope: "INCIDENT"; id: string };

function targetPath(target: CallTargetRef): string {
  return target.scope === "ALERT" ? `/alerts/${target.id}` : `/incidents/${target.id}`;
}

export const callsApi = {
  active: (target: CallTargetRef) => api.get<ActiveCallResponse>(`${targetPath(target)}/calls/active`),
  start: (target: CallTargetRef) => api.post<CallJoinResponse>(`${targetPath(target)}/calls`),
  mine: () => api.get<CallSessionResponse[]>("/calls"),
  get: (callId: string) => api.get<CallSessionResponse>(`/calls/${callId}`),
  join: (callId: string) => api.post<CallJoinResponse>(`/calls/${callId}/join`),
  decline: (callId: string) => api.post<CallSessionResponse>(`/calls/${callId}/decline`),
  leave: (callId: string) => api.post<LeaveCallResponse>(`/calls/${callId}/leave`),
  end: (callId: string) => api.post<CallSessionResponse>(`/calls/${callId}/end`),
};
