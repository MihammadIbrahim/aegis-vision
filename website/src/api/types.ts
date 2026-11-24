// Shapes returned by the Core API (see backend/docs/api_contracts.md and the live /docs schema).

export type Role = "ADMIN" | "SUPERVISOR" | "GUARD";

export interface Session {
  user_id: string;
  /** The account's internal ID (filters such as "handled by me"). */
  account_id?: string;
  full_name: string;
  role: Role;
  dashboard: string | null;
  /** The organization's Main Admin: approves campus requests and edits organization settings (D-024). */
  is_main_admin?: boolean;
}
