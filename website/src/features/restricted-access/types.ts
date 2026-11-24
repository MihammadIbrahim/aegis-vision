// Restricted-area access shapes (application/dtos/personnel_dto.py).

export interface RestrictedArea {
  area_id: string;
  area_name: string;
  building_id: string;
  building_name: string;
  floor_id: string | null;
  floor_name: string | null;
  is_restricted: boolean;
  /** Admins only: the API leaves this field out for Supervisors (an Admin note, D-016 item 16). */
  access_note?: string | null;
  marked_at: string | null;
  active_grants: number;
}

export interface Schedule {
  days: number[];
  start: string;
  end: string;
  timezone: string;
}

export interface Grant {
  id: string;
  personnel: { id: string; personnel_id: string; full_name: string; status: string; category: string; department: string | null };
  area: { id: string; name: string; floor: string | null; building: string };
  status: "ACTIVE" | "REVOKED";
  valid_from: string;
  valid_until: string | null;
  schedule: Schedule | null;
  note: string | null;
  granted_by: string | null;
  granted_at: string;
  updated_at: string | null;
  revoked_by: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
}

export interface AccessCheck {
  personnel_id: string;
  area_id: string;
  at: string;
  decision: string;
  authorized: boolean;
  grant_id: string | null;
}
