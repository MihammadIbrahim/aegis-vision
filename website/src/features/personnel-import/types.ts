// Excel/CSV import shapes (application/dtos/personnel_dto.py).

export type ImportStatus = "UPLOADED" | "INVALID" | "VALIDATED" | "IMPORTED" | "FAILED" | "ROLLED_BACK" | "DISCARDED";

export interface ImportBatch {
  id: string;
  original_filename: string;
  file_format: "XLSX" | "CSV";
  file_size: number;
  sheet_name: string | null;
  header_row: number;
  status: ImportStatus;
  total_rows: number | null;
  valid_rows: number | null;
  warning_count: number;
  error_count: number;
  imported_count: number;
  rolled_back_count: number;
  failure_reason: string | null;
  has_annotated_workbook: boolean;
  files_available: boolean;
  uploaded_by: string | null;
  uploaded_at: string;
  validated_at: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  rolled_back_by: string | null;
  rolled_back_at: string | null;
}

export type Confidence = "PROFILE" | "EXACT" | "SYNONYM" | "FUZZY" | "NONE";

export interface ImportColumn {
  index: number;
  header: string;
  target: string | null;
  confidence: Confidence;
  confirmed: boolean;
}

export interface ImportTarget {
  target: string;
  label: string;
  required: boolean;
}

export interface ImportDetail {
  batch: ImportBatch;
  sheets: string[];
  columns: ImportColumn[];
  targets: ImportTarget[];
}

export interface ImportIssue {
  row_number: number | null;
  column_index: number | null;
  column_name: string | null;
  field: string | null;
  severity: "ERROR" | "WARNING";
  code: string;
  message: string;
}

export interface PreviewRow {
  row_number: number;
  category: string;
  values: Record<string, unknown>;
  location: Record<string, string | null>;
}
