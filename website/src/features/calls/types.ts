/** Voice-call contract (decisions D-025, D-026); field names are the Core API's (call_dto.py), identical to the
 * Guard app's mobile/src/api/types.ts. */

export type CallScope = "ALERT" | "INCIDENT";
export type CallStatus = "ACTIVE" | "ENDED";
export type ParticipantState = "INVITED" | "JOINED" | "DECLINED" | "LEFT" | "MISSED";
export type EndReason = "ENDED_BY_USER" | "EVERYONE_LEFT" | "TARGET_CLOSED";
export type CallRole = "ADMIN" | "SUPERVISOR" | "GUARD";

export interface CallParticipant {
  user_id: string;
  full_name: string;
  role: CallRole;
  state: ParticipantState;
  invited_at: string;
  joined_at: string | null;
  left_at: string | null;
  is_me: boolean;
}

export interface CallSession {
  id: string;
  scope: CallScope;
  alert_id: string | null;
  incident_id: string | null;
  status: CallStatus;
  media: "AUDIO";
  started_by: string | null;
  started_by_name: string | null;
  started_by_role: CallRole;
  started_at: string;
  ended_at: string | null;
  end_reason: EndReason | null;
  joined_count: number;
  my_state: ParticipantState | null;
  can_end: boolean;
  participants: CallParticipant[];
  target_title?: string;
  target_location?: string | null;
}

export interface VoiceConnection {
  provider: "livekit";
  url: string;
  room: string;
  identity: string;
  token: string;
  expires_in: number;
}

export interface CallJoin {
  outcome: "STARTED" | "JOINED";
  call: CallSession;
  connection: VoiceConnection;
}

export interface ActiveCall {
  voice_calls_available: boolean;
  provider: "none" | "livekit";
  call: CallSession | null;
}

export interface LeaveResult {
  call: CallSession;
  call_ended: boolean;
}

export type CallTarget = { scope: "ALERT"; id: string } | { scope: "INCIDENT"; id: string };
