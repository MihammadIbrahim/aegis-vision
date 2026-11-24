import { useState } from "react";

import { Field, FormDialog } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { cameras } from "../../cameras";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { incidents, type Incident, type IncidentBody, type IncidentPriority } from "../api";

function toLocalInput(iso: string | null | undefined): string {
  const date = iso ? new Date(iso) : new Date();
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

/** Open a case (with an optional assignee and linked alert) or edit its details. */
export function IncidentForm({
  incident,
  alertId,
  initial,
  onClose,
  onSaved,
}: {
  incident?: Incident;
  alertId?: string;
  /** Prefilled values for a new case (for example from the alert it is opened from). */
  initial?: { title?: string; priority?: IncidentPriority; occurred_at?: string; camera_id?: string };
  onClose: () => void;
  onSaved: (incident: Incident) => void;
}) {
  const [title, setTitle] = useState(incident?.title ?? initial?.title ?? "");
  const [description, setDescription] = useState(incident?.description ?? "");
  const [priority, setPriority] = useState<IncidentPriority>(incident?.priority ?? initial?.priority ?? "STANDARD");
  const [occurred, setOccurred] = useState(toLocalInput(incident?.occurred_at ?? initial?.occurred_at));
  const [camera, setCamera] = useState(incident?.camera_id ?? initial?.camera_id ?? "");
  const [place, setPlace] = useState<LocationValue>(
    incident?.building_id
      ? { building_id: incident.building_id, floor_id: incident.floor_id ?? "", area_id: incident.area_id ?? "" }
      : NO_LOCATION,
  );
  const [assignee, setAssignee] = useState("");
  const cameraList = useAsync(() => cameras.list(), []);
  const tree = useAsync(() => locations.tree(), []);
  const staff = useAsync(() => (incident ? Promise.resolve([]) : incidents.staff()), [incident]);
  const valid = title.trim().length >= 3;

  async function submit() {
    const body: IncidentBody = {
      title: title.trim(),
      description: description.trim(),
      priority,
      occurred_at: occurred ? new Date(occurred).toISOString() : null,
      camera_id: camera || null,
      location: place.building_id
        ? { building_id: place.building_id, floor_id: place.floor_id || null, area_id: place.area_id || null }
        : null,
    };
    const saved = incident
      ? await incidents.update(incident.id, body)
      : await incidents.create({
          ...body,
          assigned_to: assignee || null,
          alert_ids: alertId ? [alertId] : [],
          report_ids: [],
          evidence_ids: [],
        });
    onSaved(saved);
  }

  return (
    <FormDialog title={incident ? `Edit ${incident.reference}` : "Open an incident case"} submitLabel={incident ? "Save" : "Open case"} disabled={!valid} onClose={onClose} onSubmit={submit}>
      <Field label="Title" required hint="3 to 200 characters">
        <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Description">
        <textarea value={description} maxLength={5000} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <div className="form-grid">
        <Field label="Priority" required>
          <select value={priority} onChange={(e) => setPriority(e.target.value as IncidentPriority)}>
            <option value="STANDARD">Standard</option>
            <option value="HIGH">High</option>
          </select>
        </Field>
        <Field label="When it happened" required>
          <input type="datetime-local" value={occurred} onChange={(e) => setOccurred(e.target.value)} />
        </Field>
      </div>
      <Field label="Camera" hint="The camera's location is used when you choose no location below.">
        <select value={camera} onChange={(e) => setCamera(e.target.value)}>
          <option value="">No camera</option>
          {(cameraList.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.display_name}
            </option>
          ))}
        </select>
      </Field>
      <fieldset className="stack">
        <legend className="small">Location (optional)</legend>
        <div className="row picker-row">
          <LocationPicker tree={tree.data ?? []} value={place} onChange={setPlace} anyBuildingLabel="No location" idPrefix="incident" />
        </div>
      </fieldset>
      {!incident && (
        <Field label="Assign to" hint="An Admin or Supervisor of this campus; they are notified.">
          <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
            <option value="">Not assigned</option>
            {(staff.data ?? []).map((s) => (
              <option key={s.user_id} value={s.user_id}>
                {s.full_name} ({s.role === "ADMIN" ? "Admin" : "Supervisor"})
              </option>
            ))}
          </select>
        </Field>
      )}
      {alertId && <p className="muted small">The alert you came from will be linked to the case.</p>}
    </FormDialog>
  );
}
