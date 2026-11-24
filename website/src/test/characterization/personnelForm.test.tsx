import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PersonnelFormPage } from "../../features/personnel/pages/PersonnelFormPage";
import { mockApi, renderAs, sent } from "../helpers";
import { CATEGORIES, FIELDS } from "./fixtures";

const LOAD = [
  { method: "GET", url: /\/personnel\/fields$/, body: FIELDS },
  { method: "GET", url: /\/personnel\/categories$/, body: CATEGORIES },
  { method: "GET", url: /\/personnel\/custom-fields$/, body: [] },
  { method: "GET", url: /\/locations$/, body: [] },
];

function label(text: string) {
  return screen.getByText(text, { exact: false, selector: "label" });
}

describe("characterization: personnel form", () => {
  it("marks fields required according to the chosen category", async () => {
    mockApi(LOAD);
    renderAs("ADMIN", <PersonnelFormPage />, "/personnel/new", "/personnel/new");
    await screen.findByText("Student");
    expect(within(label("Institutional ID")).getByText("(optional)")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Personnel category/), { target: { value: "cat-student" } });
    expect(within(label("Institutional ID")).getByTitle("Required")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Personnel category/), { target: { value: "cat-visitor" } });
    expect(within(label("Institutional ID")).getByText("(optional)")).toBeTruthy();
    expect(within(label("Employment / Enrollment End Date")).getByTitle("Required")).toBeTruthy();
  });

  it("sends POST /personnel with empty fields as null and no location", async () => {
    const calls = mockApi([
      ...LOAD,
      { method: "POST", url: /\/api\/v1\/personnel$/, body: { personnel: { id: "new-id" }, warnings: [] }, status: 201 },
    ]);
    renderAs("ADMIN", <PersonnelFormPage />, "/personnel/new", "/personnel/new");
    await screen.findByText("Student");
    fireEvent.change(screen.getByLabelText(/Full name/), { target: { value: "Ayesha Khan" } });
    fireEvent.change(screen.getByLabelText(/Personnel category/), { target: { value: "cat-student" } });
    fireEvent.change(screen.getByLabelText(/Institutional ID/), { target: { value: "STU-001" } });
    fireEvent.click(screen.getByRole("button", { name: "Register person" }));
    await waitFor(() => expect(sent(calls, "POST", /\/api\/v1\/personnel$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/api\/v1\/personnel$/)[0].body).toEqual({
      full_name: "Ayesha Khan", category_id: "cat-student", external_id: "STU-001", email: null, phone: null,
      department: null, designation: null, joining_date: null, start_date: null, end_date: null, address: null,
      emergency_contact_name: null, emergency_contact_phone: null, emergency_contact_relation: null, notes: null,
      custom_attributes: {}, location: null,
    });
    await screen.findByText("navigated away");
  });

  it("shows the server's field errors next to the fields", async () => {
    mockApi([
      ...LOAD,
      { method: "POST", url: /\/api\/v1\/personnel$/, status: 422, body: {
        detail: "Email Address is not a valid email address.", code: "INVALID_PERSONNEL",
        errors: [{ field: "email", code: "INVALID_EMAIL", message: "Email Address is not a valid email address." }] } },
    ]);
    renderAs("ADMIN", <PersonnelFormPage />, "/personnel/new", "/personnel/new");
    await screen.findByText("Student");
    fireEvent.change(screen.getByLabelText(/Full name/), { target: { value: "A" } });
    fireEvent.change(screen.getByLabelText(/Personnel category/), { target: { value: "cat-student" } });
    fireEvent.change(screen.getByLabelText(/Institutional ID/), { target: { value: "S-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Register person" }));
    const messages = await screen.findAllByText("Email Address is not a valid email address.");
    expect(messages.some((m) => m.className === "field-error")).toBe(true);
    expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("Email Address is not a valid email address."))).toBe(true);
  });
});
