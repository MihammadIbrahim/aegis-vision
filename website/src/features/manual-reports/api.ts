import { api, apiBlob } from "../../api/client";
import type { ManualReport } from "./types";

export const manualReports = {
  list: (limit: number, offset: number) => api<ManualReport[]>("/manual-reports", { query: { limit, offset } }),
  get: (id: string) => api<ManualReport>(`/manual-reports/${id}`),
  photo: (id: string) => apiBlob(`/manual-reports/${id}/photo`),
  review: (id: string, note: string | null) => api<ManualReport>(`/manual-reports/${id}/review`, { json: { note } }),
};
