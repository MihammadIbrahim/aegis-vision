import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../../../api/client";
import { locations, type BuildingNode } from "../../locations";
import { personnel, type PersonnelBody } from "../api";
import type { Category, CustomField, CustomValue, FieldDefinition, PersonnelDetail } from "../types";
import { Button, ErrorState, Field, LoadingState, PageHeader, useToast } from "../../../components/ui";

// The fields the form edits (the server validates the same rules as the import).
const TEXT_FIELDS = [
  "external_id",
  "email",
  "phone",
  "department",
  "designation",
  "joining_date",
  "start_date",
  "end_date",
  "address",
  "emergency_contact_name",
  "emergency_contact_phone",
  "emergency_contact_relation",
  "notes",
] as const;
type TextField = (typeof TEXT_FIELDS)[number];
type Values = { full_name: string; category_id: string } & Record<TextField, string>;

const EMPTY: Values = {
  full_name: "",
  category_id: "",
  ...(Object.fromEntries(TEXT_FIELDS.map((f) => [f, ""])) as Record<TextField, string>),
};

function inputType(kind: string): string {
  return kind === "DATE" ? "date" : kind === "EMAIL" ? "email" : kind === "PHONE" ? "tel" : "text";
}

export function PersonnelFormPage() {
  const { id } = useParams();
  const editing = Boolean(id);
  const navigate = useNavigate();
  const [fields, setFields] = useState<FieldDefinition[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [customFields, setCustomFields] = useState<CustomField[]>([]);
  const [tree, setTree] = useState<BuildingNode[]>([]);
  const [values, setValues] = useState<Values>(EMPTY);
  const [custom, setCustom] = useState<Record<string, CustomValue>>({});
  const [location, setLocation] = useState({ building_id: "", floor_id: "", area_id: "" });
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const toast = useToast();
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([personnel.fields(), personnel.categories(), personnel.customFields(), locations.tree()])
      .then(([f, c, cf, t]) => {
        setFields(f);
        setCategories(c);
        setCustomFields(cf.filter((x) => x.is_active));
        setTree(t);
        setLoaded(true);
      })
      .catch(setError);
    if (id) {
      personnel
        .details(id)
        .then((p) => {
          const d = p as PersonnelDetail;
          const loaded: Values = { ...EMPTY, full_name: d.full_name, category_id: d.category.id };
          for (const f of TEXT_FIELDS) loaded[f] = (d[f] as string | null) ?? "";
          setValues(loaded);
          setCustom(d.custom_attributes);
          if (d.location)
            setLocation({
              building_id: d.location.building_id,
              floor_id: d.location.floor_id ?? "",
              area_id: d.location.area_id ?? "",
            });
        })
        .catch(setError);
    }
  }, [id]);

  const category = categories.find((c) => c.id === values.category_id);
  const required = useMemo(() => {
    const set = new Set(fields.filter((f) => f.always_required).map((f) => f.key));
    category?.required_fields.forEach((f) => set.add(f));
    return set;
  }, [fields, category]);
  const applicable = customFields.filter((f) => f.category_ids.length === 0 || f.category_ids.includes(values.category_id));
  const building = tree.find((b) => b.id === location.building_id);
  const floor = building?.floors.find((f) => f.id === location.floor_id);
  const areaChoices = floor ? floor.areas : (building?.areas ?? []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setFieldErrors({});
    const body: PersonnelBody = { full_name: values.full_name, category_id: values.category_id };
    for (const f of TEXT_FIELDS) body[f] = values[f].trim() === "" ? null : values[f];
    body.custom_attributes = Object.fromEntries(
      Object.entries(custom).filter(([key, v]) => v !== "" && v !== null && applicable.some((f) => f.key === key)),
    );
    body.location = location.building_id
      ? { building_id: location.building_id, floor_id: location.floor_id || null, area_id: location.area_id || null }
      : null;
    try {
      const saved = editing && id ? await personnel.update(id, body) : await personnel.create(body);
      // Warnings never block saving; they are shown on the next page instead of delaying it.
      if (saved.warnings.length) toast("warning", `Saved with warnings: ${saved.warnings.map((w) => w.message).join(" ")}`);
      else toast("success", editing ? "Changes saved." : "Person registered.");
      navigate(`/personnel/${saved.personnel.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.errors.length) {
        setFieldErrors(Object.fromEntries(e.errors.map((x) => [x.field ?? "", x.message])));
      }
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  const mark = (key: string) => (required.has(key) ? <span className="req" title="Required">*</span> : <span className="opt">(optional)</span>);
  const fieldError_ = (key: string) => fieldErrors[key];
  const fieldError = (key: string) => (fieldErrors[key] ? <span className="field-error">{fieldErrors[key]}</span> : null);

  return (
    <section>
      <PageHeader
        back={id ? { to: `/personnel/${id}`, label: "Back to the person" } : { to: "/personnel", label: "Personnel Directory" }}
        title={editing ? "Edit person" : "Add person"}
        description={
          <>
            Fields marked <span className="req">*</span> are required. The Personnel ID is created by the system.
          </>
        }
      />
      {error ? <ErrorState error={error} /> : null}
      {!loaded && <LoadingState label="Loading the form…" />}
      <form onSubmit={submit} className="card form-grid">
        <Field label="Full name" required error={fieldError_("full_name")}>
          <input value={values.full_name} onChange={(e) => setValues({ ...values, full_name: e.target.value })} />
        </Field>
        <Field label="Personnel category" required error={fieldError_("category")}>
          <select value={values.category_id} onChange={(e) => setValues({ ...values, category_id: e.target.value })}>
            <option value="">Choose…</option>
            {categories
              .filter((c) => c.is_active || c.id === values.category_id)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
          </select>
        </Field>
        {fields
          .filter((f) => (TEXT_FIELDS as readonly string[]).includes(f.key))
          .map((f) => (
            <Field
              key={f.key}
              className={f.key === "notes" || f.key === "address" ? "wide" : undefined}
              label={f.label}
              required={required.has(f.key)}
              hint={f.help || undefined}
              error={fieldError_(f.key)}
            >
              {f.key === "notes" || f.key === "address" ? (
                <textarea
                  value={values[f.key as TextField]}
                  maxLength={f.max_length ?? undefined}
                  onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                />
              ) : (
                <input
                  type={inputType(f.kind)}
                  value={values[f.key as TextField]}
                  maxLength={f.max_length ?? undefined}
                  onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                />
              )}
            </Field>
          ))}
        <fieldset className="wide">
          <legend>Assigned location {mark("assigned_location")}</legend>
          <select
            aria-label="Building"
            value={location.building_id}
            onChange={(e) => setLocation({ building_id: e.target.value, floor_id: "", area_id: "" })}
          >
            <option value="">No location</option>
            {tree.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
          {building && building.floors.length > 0 && (
            <select
              aria-label="Floor"
              value={location.floor_id}
              onChange={(e) => setLocation({ ...location, floor_id: e.target.value, area_id: "" })}
            >
              <option value="">Whole building</option>
              {building.floors.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          )}
          {building && areaChoices.length > 0 && (
            <select aria-label="Area" value={location.area_id} onChange={(e) => setLocation({ ...location, area_id: e.target.value })}>
              <option value="">{floor ? "Whole floor" : "No area"}</option>
              {areaChoices.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          )}
          {fieldError("assigned_location")}
        </fieldset>
        {applicable.length > 0 && (
          <fieldset className="wide">
            <legend>Additional fields</legend>
            <div className="form-grid">
              {applicable.map((f) => {
                const key = `custom:${f.key}`;
                const value = custom[f.key];
                return (
                  <label key={f.id}>
                    {f.label} {f.is_required ? <span className="req">*</span> : <span className="opt">(optional)</span>}
                    {f.field_type === "CHOICE" ? (
                      <select value={String(value ?? "")} onChange={(e) => setCustom({ ...custom, [f.key]: e.target.value })}>
                        <option value="">—</option>
                        {f.choices.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                    ) : f.field_type === "BOOLEAN" ? (
                      <select
                        value={value === true ? "true" : value === false ? "false" : ""}
                        onChange={(e) => setCustom({ ...custom, [f.key]: e.target.value === "" ? null : e.target.value === "true" })}
                      >
                        <option value="">—</option>
                        <option value="true">Yes</option>
                        <option value="false">No</option>
                      </select>
                    ) : (
                      <input
                        type={f.field_type === "NUMBER" ? "number" : inputType(f.field_type)}
                        value={value === null || value === undefined ? "" : String(value)}
                        onChange={(e) =>
                          setCustom({ ...custom, [f.key]: f.field_type === "NUMBER" && e.target.value ? Number(e.target.value) : e.target.value })
                        }
                      />
                    )}
                    {f.help_text && <span className="hint">{f.help_text}</span>}
                    {fieldError(key)}
                  </label>
                );
              })}
            </div>
          </fieldset>
        )}
        <div className="wide actions">
          <Button type="submit" loading={saving}>
            {editing ? "Save changes" : "Register person"}
          </Button>
          <Button variant="secondary" onClick={() => navigate(-1)}>
            Cancel
          </Button>
        </div>
      </form>
    </section>
  );
}
