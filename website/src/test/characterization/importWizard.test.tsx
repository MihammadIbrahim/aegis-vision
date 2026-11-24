import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ImportWizardPage } from "../../features/personnel-import/pages/ImportWizardPage";
import { mockApi, renderAs, sent } from "../helpers";
import { importDetail } from "./fixtures";

describe("characterization: import wizard", () => {
  it("uploads the file as multipart with the header row", async () => {
    const calls = mockApi([{ method: "POST", url: /\/personnel-imports$/, body: importDetail("UPLOADED"), status: 201 }]);
    renderAs("ADMIN", <ImportWizardPage />, "/personnel/imports/new", "/personnel/imports/new");
    const file = new File(["Name\nA\n"], "staff.csv", { type: "text/csv" });
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText(/Excel \(.xlsx\) or CSV/), file);
    // jsdom does not count a test-uploaded file for the input's "required" check (browsers do; the
    // live smoke run covers that), so the form is submitted directly: the page's own handler still runs.
    fireEvent.submit(screen.getByRole("button", { name: "Upload and read columns" }).closest("form") as HTMLFormElement);
    await waitFor(() => expect(sent(calls, "POST", /\/personnel-imports$/)).toHaveLength(1));
    const form = sent(calls, "POST", /\/personnel-imports$/)[0].body as FormData;
    expect(form).toBeInstanceOf(FormData);
    expect((form.get("file") as File).name).toBe("staff.csv");
    expect(form.get("header_row")).toBe("1");
    expect(form.has("sheet_name")).toBe(false);
  });

  it("refuses to import an INVALID batch and offers the error workbook", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/personnel-imports\/b1$/, body: importDetail("INVALID", { error_count: 2, has_annotated_workbook: true }) },
      { method: "GET", url: /\/personnel-imports\/b1\/issues/, body: [
        { row_number: 3, column_index: 0, column_name: "Name", field: "full_name", severity: "ERROR", code: "REQUIRED", message: "Full Name is required." }] },
    ]);
    renderAs("ADMIN", <ImportWizardPage />, "/personnel/imports/b1", "/personnel/imports/:id");
    await screen.findByText(/Nothing will be imported while blocking errors remain/);
    expect(screen.getByRole("button", { name: "Download error workbook" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Upload corrected file" })).toBeTruthy();
    expect(await screen.findByText("Full Name is required.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Import all/ })).toBeNull();
    expect(calls.some((c) => /preview|confirm/.test(c.url))).toBe(false);
  });

  it("requires warnings to be acknowledged, then confirms with acknowledge_warnings", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/personnel-imports\/b1$/, body: importDetail("VALIDATED", { warning_count: 2, has_annotated_workbook: true }) },
      { method: "GET", url: /\/personnel-imports\/b1\/issues/, body: [] },
      { method: "GET", url: /\/personnel-imports\/b1\/preview/, body: [
        { row_number: 2, category: "Student", values: { full_name: "Ali", external_id: "S1", email: null }, location: {} }] },
      { method: "POST", url: /\/personnel-imports\/b1\/confirm$/, body: importDetail("IMPORTED", { imported_count: 3 }) },
    ]);
    renderAs("ADMIN", <ImportWizardPage />, "/personnel/imports/b1", "/personnel/imports/:id");
    await screen.findByText("Ali");
    const importButton = screen.getByRole("button", { name: "Import all 3 records" }) as HTMLButtonElement;
    expect(importButton.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(importButton.disabled).toBe(false);
    fireEvent.click(importButton);
    await waitFor(() => expect(sent(calls, "POST", /confirm$/)).toHaveLength(1));
    expect(sent(calls, "POST", /confirm$/)[0].body).toEqual({ acknowledge_warnings: true });
    await screen.findByText("3 personnel records were imported.");
  });
});
