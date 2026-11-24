import { api, type RequestOptions } from "../../api/client";
import type { ImportBatch, ImportDetail, ImportIssue, PreviewRow } from "./types";

export const imports = {
  upload: (file: File, sheetName: string, headerRow: number) => {
    const form = new FormData();
    form.append("file", file);
    if (sheetName) form.append("sheet_name", sheetName);
    form.append("header_row", String(headerRow));
    return api<ImportDetail>("/personnel-imports", { form });
  },
  history: (limit = 50, offset = 0) => api<ImportBatch[]>("/personnel-imports", { query: { limit, offset } }),
  get: (id: string) => api<ImportDetail>(`/personnel-imports/${id}`),
  map: (id: string, columns: { index: number; target: string }[], saveProfileAs: string | null) =>
    api<ImportDetail>(`/personnel-imports/${id}/mapping`, {
      method: "PUT",
      json: { columns, save_profile_as: saveProfileAs },
    }),
  issues: (id: string, query: RequestOptions["query"] = {}) =>
    api<ImportIssue[]>(`/personnel-imports/${id}/issues`, { query }),
  preview: (id: string, limit = 50, offset = 0) =>
    api<PreviewRow[]>(`/personnel-imports/${id}/preview`, { query: { limit, offset } }),
  confirm: (id: string, acknowledgeWarnings: boolean) =>
    api<ImportDetail>(`/personnel-imports/${id}/confirm`, { json: { acknowledge_warnings: acknowledgeWarnings } }),
  rollback: (id: string, reason: string | null) =>
    api<ImportDetail>(`/personnel-imports/${id}/rollback`, { json: { reason } }),
  discard: (id: string) => api<void>(`/personnel-imports/${id}`, { method: "DELETE" }),
  /** Saved column mappings: applied automatically to later uploads with the same headers. */
  profiles: () => api<MappingProfile[]>("/personnel-imports/mapping-profiles"),
  deleteProfile: (id: string) => api<void>(`/personnel-imports/mapping-profiles/${id}`, { method: "DELETE" }),
};

export interface MappingProfile {
  id: string;
  name: string;
  /** Spreadsheet header → directory field (or IGNORE). */
  mapping: Record<string, string>;
}
