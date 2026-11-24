import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ImportDetail } from "../features/personnel-import";
import { MappingStep } from "../features/personnel-import/pages/ImportWizardPage";
import { mockApi, renderAs } from "./helpers";

const detail: ImportDetail = {
  batch: {
    id: "b1", original_filename: "students.xlsx", file_format: "XLSX", file_size: 10, sheet_name: "Sheet", header_row: 1,
    status: "UPLOADED", total_rows: null, valid_rows: null, warning_count: 0, error_count: 0, imported_count: 0,
    rolled_back_count: 0, failure_reason: null, has_annotated_workbook: false, files_available: true, uploaded_by: "A",
    uploaded_at: "2026-11-01T10:00:00Z", validated_at: null, confirmed_by: null, confirmed_at: null, rolled_back_by: null,
    rolled_back_at: null,
  },
  sheets: ["Sheet"],
  columns: [
    { index: 0, header: "Student Name", target: "full_name", confidence: "SYNONYM", confirmed: false },
    { index: 1, header: "Type", target: "category", confidence: "SYNONYM", confirmed: false },
    { index: 2, header: "Hobby", target: null, confidence: "NONE", confirmed: false },
  ],
  targets: [
    { target: "full_name", label: "Full Name", required: true },
    { target: "category", label: "Personnel Category", required: true },
    { target: "email", label: "Email Address", required: false },
    { target: "IGNORE", label: "Do not import this column", required: false },
  ],
};

describe("column mapping", () => {
  it("needs a decision for every column before validating", async () => {
    const calls = mockApi([[/\/personnel-imports\/b1\/mapping/, { ...detail, batch: { ...detail.batch, status: "VALIDATED" } }]]);
    const onValidated = vi.fn();
    renderAs("ADMIN", <MappingStep detail={detail} onValidated={onValidated} />);
    const validate = screen.getByRole("button", { name: /Confirm mapping/ });
    expect((validate as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("1 column(s) still need a decision.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Field for Hobby"), { target: { value: "IGNORE" } });
    expect((validate as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(validate);
    await waitFor(() => expect(onValidated).toHaveBeenCalled());
    const body = calls[0].body as { columns: unknown };
    expect(calls[0].method).toBe("PUT");
    expect(body.columns).toEqual([
      { index: 0, target: "full_name" },
      { index: 1, target: "category" },
      { index: 2, target: "IGNORE" },
    ]);
  });

  it("will not validate while a required field is unmapped", () => {
    const missing = { ...detail, columns: detail.columns.map((c) => (c.index === 1 ? { ...c, target: null } : c)) };
    renderAs("ADMIN", <MappingStep detail={missing} onValidated={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Field for Type"), { target: { value: "IGNORE" } });
    fireEvent.change(screen.getByLabelText("Field for Hobby"), { target: { value: "IGNORE" } });
    expect(screen.getByText(/Required fields not mapped: Personnel Category/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Confirm mapping/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
