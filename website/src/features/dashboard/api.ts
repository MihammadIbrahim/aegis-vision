import { api } from "../../api/client";
import type { AdminDashboardData, SupervisorDashboardData } from "./types";

/** Lists are read with the largest page the API allows; counts above it are shown as "at least". */
export const LIST_CAP = 500;

export const dashboard = {
  admin: () => api<AdminDashboardData>("/organization-admin/dashboard"),
  supervisor: () => api<SupervisorDashboardData>("/supervisor/dashboard"),
};
