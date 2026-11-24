import { api, buildUrl, download } from "../../api/client";

// User Records & Rejoining, Guard ID regeneration, permanent deletion (Org Admin pages 11–13; D-024).

export interface UserRecord {
  user_id: string;
  display_id: string;
  role: "SUPERVISOR" | "GUARD";
  status: "ACTIVE" | "LEFT";
  full_name: string;
  username: string | null;
  email: string | null;
  phone_number: string | null;
  created_at: string;
  deleted: boolean;
  deleted_at: string | null;
  location: string | null;
  status_changed_at: string | null;
}

export interface RecordDetail {
  record: UserRecord;
  history: { from_status: string | null; to_status: string; event: string; changed_at: string; changed_by_name: string | null }[];
  activity: { occurred_at: string; action: string; category: string; outcome: string; target_type: string; by_user: boolean }[];
}

export interface RecordQuery {
  role?: string;
  status?: string;
  q?: string;
  include_deleted?: boolean;
  limit: number;
  offset: number;
}

export interface RejoinBody {
  full_name: string;
  username?: string | null;
  phone_number?: string | null;
  new_password?: string | null;
  location?: { building_id: string; floor_id: string | null; area_id: string | null } | null;
  current_password: string;
}

export const userRecords = {
  list: (query: RecordQuery) =>
    api<{ items: UserRecord[]; total: number; limit: number; offset: number }>("/user-records", { query: { ...query } }),
  detail: (id: string) => api<RecordDetail>(`/user-records/${id}`),
  lookup: (display_id: string) => api<UserRecord>("/user-records/lookup", { query: { display_id } }),
  rejoin: (id: string, body: RejoinBody) => api<UserRecord>(`/user-records/${id}/rejoin`, { json: body }),
  remove: (id: string, current_password: string, confirmation: string) =>
    api<void>(`/user-records/${id}/permanent-delete`, { json: { current_password, confirmation } }),
  exportCsv: (query: Omit<RecordQuery, "limit" | "offset">) =>
    download(buildUrl("/user-records/export", { ...query }).replace(/^\/api\/v1/, ""), "user-records.csv"),
  regenerateGuardId: (id: string, current_password: string) =>
    api<{ display_id: string }>(`/staff/guards/${id}/regenerate-id`, { json: { current_password } }),
};

export const EVENT_LABELS: Record<string, string> = {
  CREATED: "Account created",
  DEACTIVATED: "Deactivated (left)",
  REACTIVATED: "Reactivated / rejoined",
  PERMANENTLY_DELETED: "Personal data deleted",
};
