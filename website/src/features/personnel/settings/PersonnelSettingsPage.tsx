import { useCallback, useEffect, useState, type FormEvent } from "react";

import { personnel } from "../api";
import type { Category, CustomField, FieldDefinition, FieldType } from "../types";
import { Plus } from "lucide-react";

import { Button, ErrorState, PageHeader, useToast } from "../../../components/ui";

const TYPES: FieldType[] = ["TEXT", "NUMBER", "DATE", "BOOLEAN", "CHOICE", "EMAIL", "PHONE"];

export function PersonnelSettingsPage() {
  const [fields, setFields] = useState<FieldDefinition[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [customFields, setCustomFields] = useState<CustomField[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [newCategory, setNewCategory] = useState({ code: "", label: "" });
  const [newField, setNewField] = useState({ key: "", label: "", field_type: "TEXT" as FieldType, choices: "", is_required: false });

  const load = useCallback(() => {
    Promise.all([personnel.fields(), personnel.categories(), personnel.customFields()])
      .then(([f, c, cf]) => {
        setFields(f);
        setCategories(c);
        setCustomFields(cf);
      })
      .catch(setError);
  }, []);
  useEffect(load, [load]);
  const requirable = fields.filter((f) => f.can_be_required_by_category);

  const toast = useToast();

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      toast("success", "Saved.");
      load();
    } catch (e) {
      setError(e);
    }
  }

  function toggleRequired(category: Category, key: string) {
    const required = category.required_fields.includes(key)
      ? category.required_fields.filter((f) => f !== key)
      : [...category.required_fields, key];
    void act(() => personnel.updateCategory(category.id, { ...category, required_fields: required }));
  }

  function addCategory(event: FormEvent) {
    event.preventDefault();
    void act(async () => {
      await personnel.addCategory({ code: newCategory.code, label: newCategory.label });
      setNewCategory({ code: "", label: "" });
    });
  }

  function addField(event: FormEvent) {
    event.preventDefault();
    void act(async () => {
      await personnel.addCustomField({
        key: newField.key,
        label: newField.label,
        field_type: newField.field_type,
        is_required: newField.is_required,
        choices: newField.field_type === "CHOICE" ? newField.choices.split(",").map((c) => c.trim()).filter(Boolean) : [],
      });
      setNewField({ key: "", label: "", field_type: "TEXT", choices: "", is_required: false });
    });
  }

  return (
    <section>
      <PageHeader
        title="Personnel categories and fields"
        description={
          <>
        Categories and extra fields belong to this campus. New spreadsheet columns never create database columns: map them to a
        custom field defined here, or ignore them.
      </>
        }
      />
      {error ? <ErrorState error={error} /> : null}
      <div className="card">
        <h2>Categories</h2>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Code</th>
              <th>Also required</th>
              <th>In use</th>
            </tr>
          </thead>
          <tbody>
            {categories.map((c) => (
              <tr key={c.id}>
                <td>
                  <input
                    aria-label={`Name of ${c.code}`}
                    defaultValue={c.label}
                    onBlur={(e) => e.target.value !== c.label && void act(() => personnel.updateCategory(c.id, { ...c, label: e.target.value }))}
                  />
                </td>
                <td className="mono">{c.code}</td>
                <td className="small">
                  {requirable.map((f) => (
                    <label key={f.key} className="checkbox inline">
                      <input type="checkbox" checked={c.required_fields.includes(f.key)} onChange={() => toggleRequired(c, f.key)} />
                      {f.label}
                    </label>
                  ))}
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${c.label} in use`}
                    checked={c.is_active}
                    onChange={(e) => void act(() => personnel.updateCategory(c.id, { ...c, is_active: e.target.checked }))}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <form className="row" onSubmit={addCategory}>
          <input placeholder="Code, e.g. INTERN" value={newCategory.code} required onChange={(e) => setNewCategory({ ...newCategory, code: e.target.value })} />
          <input placeholder="Name, e.g. Intern" value={newCategory.label} required onChange={(e) => setNewCategory({ ...newCategory, label: e.target.value })} />
          <Button type="submit" icon={Plus}>
            Add category
          </Button>
        </form>
      </div>
      <div className="card">
        <h2>Custom fields</h2>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Key</th>
              <th>Type</th>
              <th>Required</th>
              <th>Used for</th>
              <th>In use</th>
            </tr>
          </thead>
          <tbody>
            {customFields.map((f) => (
              <tr key={f.id}>
                <td>{f.label}</td>
                <td className="mono">{f.key}</td>
                <td>
                  {f.field_type}
                  {f.choices.length > 0 && <div className="small muted">{f.choices.join(", ")}</div>}
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${f.label} required`}
                    checked={f.is_required}
                    onChange={(e) => void act(() => personnel.updateCustomField(f.id, { ...f, is_required: e.target.checked }))}
                  />
                </td>
                <td className="small">
                  {f.category_ids.length === 0
                    ? "All categories"
                    : f.category_ids.map((id) => categories.find((c) => c.id === id)?.label ?? "?").join(", ")}
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${f.label} in use`}
                    checked={f.is_active}
                    onChange={(e) => void act(() => personnel.updateCustomField(f.id, { ...f, is_active: e.target.checked }))}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <form className="row" onSubmit={addField}>
          <input placeholder="Key, e.g. academic_program" value={newField.key} required onChange={(e) => setNewField({ ...newField, key: e.target.value })} />
          <input placeholder="Name, e.g. Academic Program" value={newField.label} required onChange={(e) => setNewField({ ...newField, label: e.target.value })} />
          <select aria-label="Type" value={newField.field_type} onChange={(e) => setNewField({ ...newField, field_type: e.target.value as FieldType })}>
            {TYPES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          {newField.field_type === "CHOICE" && (
            <input placeholder="Choices, comma-separated" value={newField.choices} onChange={(e) => setNewField({ ...newField, choices: e.target.value })} />
          )}
          <label className="checkbox">
            <input type="checkbox" checked={newField.is_required} onChange={(e) => setNewField({ ...newField, is_required: e.target.checked })} />
            Required
          </label>
          <Button type="submit" icon={Plus}>
            Add field
          </Button>
        </form>
      </div>
    </section>
  );
}
