// Personnel shapes returned by the Core API (application/dtos/personnel_dto.py).

export interface CategoryRef {
  id: string;
  code: string;
  label: string;
}

export interface AssignedLocation {
  building: string | null;
  floor: string | null;
  area: string | null;
}

export interface PersonnelBasic {
  id: string;
  personnel_id: string;
  external_id: string | null;
  full_name: string;
  category: CategoryRef;
  department: string | null;
  designation: string | null;
  status: "ACTIVE" | "INACTIVE";
  start_date: string | null;
  end_date: string | null;
  assigned_location: AssignedLocation | null;
}

export interface DirectoryRow extends PersonnelBasic {
  email: string | null; // Admins only
  phone: string | null; // Admins only
}

export interface Directory {
  items: DirectoryRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface LocationPath {
  level: "BUILDING" | "FLOOR" | "AREA";
  building_id: string;
  building_name: string;
  floor_id: string | null;
  floor_name: string | null;
  area_id: string | null;
  area_name: string | null;
}

export type CustomValue = string | number | boolean | null;

export interface PersonnelDetail {
  id: string;
  personnel_id: string;
  external_id: string | null;
  full_name: string;
  category: CategoryRef;
  email: string | null;
  phone: string | null;
  department: string | null;
  designation: string | null;
  joining_date: string | null;
  start_date: string | null;
  end_date: string | null;
  status: "ACTIVE" | "INACTIVE";
  address: string | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  emergency_contact_relation: string | null;
  notes: string | null;
  custom_attributes: Record<string, CustomValue>;
  location: LocationPath | null;
  has_photo: boolean;
  created_via: "MANUAL" | "IMPORT";
  import_batch_id: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_by: string | null;
  updated_at: string | null;
  deactivated_by: string | null;
  deactivated_at: string | null;
  deactivation_reason: string | null;
}

export interface Issue {
  field: string | null;
  code: string;
  message: string;
}

export interface PersonnelWrite {
  personnel: PersonnelDetail;
  warnings: Issue[];
}

export interface FieldDefinition {
  key: string;
  label: string;
  kind: string;
  always_required: boolean;
  can_be_required_by_category: boolean;
  max_length: number | null;
  help: string;
}

export interface Category {
  id: string;
  code: string;
  label: string;
  description: string | null;
  required_fields: string[];
  is_system: boolean;
  is_active: boolean;
  sort_order: number;
}

export type FieldType = "TEXT" | "NUMBER" | "DATE" | "BOOLEAN" | "CHOICE" | "EMAIL" | "PHONE";

export interface CustomField {
  id: string;
  key: string;
  label: string;
  field_type: FieldType;
  choices: string[];
  is_required: boolean;
  category_ids: string[];
  max_length: number;
  help_text: string | null;
  is_active: boolean;
}

export interface AuditEntry {
  occurred_at: string;
  action: string;
  target_type: string;
  target_id: string | null;
  actor_role: string;
  actor_id: string | null;
  actor_name: string | null;
  details: Record<string, unknown>;
}
