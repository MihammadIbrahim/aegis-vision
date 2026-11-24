// Response shapes copied from the Core API DTOs (application/dtos/personnel_dto.py), used to pin
// down how the current pages behave before the Phase 4 refactor.

export const FIELDS = [
  { key: "full_name", label: "Full Name", kind: "TEXT", always_required: true, can_be_required_by_category: false, max_length: 200, help: "" },
  { key: "category", label: "Personnel Category", kind: "CATEGORY", always_required: true, can_be_required_by_category: false, max_length: null, help: "" },
  { key: "external_id", label: "Institutional ID", kind: "TEXT", always_required: false, can_be_required_by_category: true, max_length: 64, help: "" },
  { key: "email", label: "Email Address", kind: "EMAIL", always_required: false, can_be_required_by_category: true, max_length: 320, help: "" },
  { key: "phone", label: "Phone Number", kind: "PHONE", always_required: false, can_be_required_by_category: true, max_length: null, help: "" },
  { key: "end_date", label: "Employment / Enrollment End Date", kind: "DATE", always_required: false, can_be_required_by_category: true, max_length: null, help: "" },
];

export const CATEGORIES = [
  { id: "cat-student", code: "STUDENT", label: "Student", description: null, required_fields: ["external_id"], is_system: true, is_active: true, sort_order: 10 },
  { id: "cat-visitor", code: "VISITOR", label: "Visitor", description: null, required_fields: ["end_date"], is_system: true, is_active: true, sort_order: 70 },
];

export const DETAIL = {
  id: "p1", personnel_id: "PSN-ABCDEFGHJK", external_id: "STU-001", full_name: "Ayesha Khan",
  category: { id: "cat-student", code: "STUDENT", label: "Student" }, email: "ayesha@example.com", phone: "03001234567",
  department: "CS", designation: null, joining_date: null, start_date: null, end_date: null, status: "ACTIVE",
  address: "Private Street 1", emergency_contact_name: null, emergency_contact_phone: null,
  emergency_contact_relation: null, notes: null, custom_attributes: {}, location: null, has_photo: false,
  created_via: "MANUAL", import_batch_id: null, created_by: "Admin A", created_at: "2026-11-01T10:00:00Z",
  updated_by: "Admin A", updated_at: "2026-11-01T10:00:00Z", deactivated_by: null, deactivated_at: null,
  deactivation_reason: null,
};

export const BASIC = {
  id: "p1", personnel_id: "PSN-ABCDEFGHJK", external_id: "STU-001", full_name: "Ayesha Khan",
  category: { id: "cat-student", code: "STUDENT", label: "Student" }, department: "CS", designation: null,
  status: "ACTIVE", start_date: null, end_date: null, assigned_location: null,
};

export const GRANT = {
  id: "g1",
  personnel: { id: "p1", personnel_id: "PSN-ABCDEFGHJK", full_name: "Ayesha Khan", status: "ACTIVE", category: "Student", department: "CS" },
  area: { id: "a1", name: "Server Room", floor: "Ground", building: "IT Block" },
  status: "ACTIVE", valid_from: "2026-11-01T00:00:00Z", valid_until: null, schedule: null, note: null,
  granted_by: "Admin A", granted_at: "2026-11-01T00:00:00Z", updated_at: null, revoked_by: null, revoked_at: null,
  revoke_reason: null,
};

export const AREA = {
  area_id: "a1", area_name: "Server Room", building_id: "b1", building_name: "IT Block", floor_id: "f1",
  floor_name: "Ground", is_restricted: true, access_note: null, marked_at: "2026-11-01T00:00:00Z", active_grants: 1,
};

export function batch(status: string, extra: Record<string, unknown> = {}) {
  return {
    id: "b1", original_filename: "staff.xlsx", file_format: "XLSX", file_size: 100, sheet_name: "Sheet", header_row: 1,
    status, total_rows: 3, valid_rows: 3, warning_count: 0, error_count: 0, imported_count: 0, rolled_back_count: 0,
    failure_reason: null, has_annotated_workbook: false, files_available: true, uploaded_by: "Admin A",
    uploaded_at: "2026-11-01T10:00:00Z", validated_at: null, confirmed_by: null, confirmed_at: null,
    rolled_back_by: null, rolled_back_at: null, ...extra,
  };
}

export function importDetail(status: string, extra: Record<string, unknown> = {}) {
  return {
    batch: batch(status, extra),
    sheets: [],
    columns: [{ index: 0, header: "Name", target: "full_name", confidence: "EXACT", confirmed: status !== "UPLOADED" }],
    targets: [{ target: "full_name", label: "Full Name", required: true }, { target: "IGNORE", label: "Do not import this column", required: false }],
  };
}
