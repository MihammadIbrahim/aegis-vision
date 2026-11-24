import { api, apiBlob } from "../../api/client";
import type {
  AuditEntry,
  Category,
  CustomField,
  Directory,
  FieldDefinition,
  PersonnelBasic,
  PersonnelDetail,
  PersonnelWrite,
} from "./types";

export interface DirectoryQuery {
  q?: string;
  category_id?: string;
  status?: string;
  department?: string;
  designation?: string;
  area_id?: string;
  limit?: number;
  offset?: number;
}

export interface PersonnelBody {
  full_name: string;
  category_id: string;
  external_id?: string | null;
  email?: string | null;
  phone?: string | null;
  department?: string | null;
  designation?: string | null;
  joining_date?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  address?: string | null;
  emergency_contact_name?: string | null;
  emergency_contact_phone?: string | null;
  emergency_contact_relation?: string | null;
  notes?: string | null;
  custom_attributes?: Record<string, string | number | boolean | null>;
  location?: { building_id: string; floor_id?: string | null; area_id?: string | null } | null;
}

export const personnel = {
  fields: () => api<FieldDefinition[]>("/personnel/fields"),
  categories: () => api<Category[]>("/personnel/categories"),
  addCategory: (body: Partial<Category> & { code: string; label: string }) =>
    api<Category>("/personnel/categories", { json: body }),
  updateCategory: (id: string, body: Partial<Category>) =>
    api<Category>(`/personnel/categories/${id}`, { method: "PUT", json: body }),
  customFields: () => api<CustomField[]>("/personnel/custom-fields"),
  addCustomField: (body: Partial<CustomField> & { key: string; label: string; field_type: string }) =>
    api<CustomField>("/personnel/custom-fields", { json: body }),
  updateCustomField: (id: string, body: Partial<CustomField>) =>
    api<CustomField>(`/personnel/custom-fields/${id}`, { method: "PUT", json: body }),
  directory: (query: DirectoryQuery) => api<Directory>("/personnel", { query: { ...query } }),
  details: (id: string) => api<PersonnelDetail | PersonnelBasic>(`/personnel/${id}`),
  create: (body: PersonnelBody) => api<PersonnelWrite>("/personnel", { json: body }),
  update: (id: string, body: PersonnelBody) => api<PersonnelWrite>(`/personnel/${id}`, { method: "PUT", json: body }),
  deactivate: (id: string, reason: string | null) =>
    api<PersonnelDetail>(`/personnel/${id}/deactivate`, { json: { reason } }),
  reactivate: (id: string) => api<PersonnelDetail>(`/personnel/${id}/reactivate`, { method: "POST" }),
  history: (id: string) => api<AuditEntry[]>(`/personnel/${id}/history`),
  photo: (id: string) => apiBlob(`/personnel/${id}/photo`),
  uploadPhoto: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api<PersonnelDetail>(`/personnel/${id}/photo`, { method: "PUT", form });
  },
  removePhoto: (id: string) => api<void>(`/personnel/${id}/photo`, { method: "DELETE" }),
};
