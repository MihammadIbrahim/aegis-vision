import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "../../../api/client";
import { Button, Dialog, Field } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { staff } from "../../staff";
import type { EscalationBody, EscalationScope } from "../types";

const SCOPES: Array<[EscalationScope, string, string]> = [
  ["CAMPUS", "Every Guard of this campus", "All active Guards of the alert's campus who have not received it yet."],
  ["LOCATION", "Guards of a location", "Guards assigned at or below a building, floor or area of this campus."],
  ["GUARDS", "Chosen Guards", "Guards of this campus, found by name or by Guard ID."],
];

/** Splits "ID1, ID2\nID3" into IDs. */
export function parseGuardIds(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Sends the alert to more Guards of the same campus (requirements 15.4). Never to another campus. */
export function EscalateDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (body: EscalationBody) => Promise<void>;
}) {
  const tree = useAsync(() => locations.tree(), []);
  const [scope, setScope] = useState<EscalationScope>("CAMPUS");
  const [location, setLocation] = useState<LocationValue>(NO_LOCATION);
  const [guardText, setGuardText] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  const found = useAsync(
    () => (scope === "GUARDS" && query ? staff.list({ role: "GUARD", status: "ACTIVE", q: query, limit: 10, offset: 0 }) : Promise.resolve([])),
    [scope, query],
  );

  const guardIds = parseGuardIds(guardText);
  function add(id: string) {
    if (!guardIds.includes(id)) setGuardText([...guardIds, id].join("\n"));
  }
  const incomplete = (scope === "LOCATION" && !location.building_id) || (scope === "GUARDS" && guardIds.length === 0);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (incomplete) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        scope,
        location:
          scope === "LOCATION"
            ? { building_id: location.building_id, floor_id: location.floor_id || null, area_id: location.area_id || null }
            : null,
        guard_ids: scope === "GUARDS" ? guardIds : [],
        note: note.trim() || null,
      });
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open title="Escalate alert" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <fieldset>
          <legend>Send to</legend>
          {SCOPES.map(([value, label, hint]) => (
            <label key={value} className="checkbox choice">
              <input type="radio" name="scope" value={value} checked={scope === value} onChange={() => setScope(value)} />
              <span>
                {label}
                <span className="hint block">{hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {scope === "LOCATION" && (
          <div className="row picker-row" role="group" aria-label="Location">
            <LocationPicker tree={tree.data ?? []} value={location} onChange={setLocation} anyBuildingLabel="Choose a building" />
          </div>
        )}
        {scope === "GUARDS" && (
          <Field label="Find a Guard" hint="Active Guards of this campus by name, username or Guard ID.">
            <input type="search" value={search} maxLength={200} onChange={(e) => setSearch(e.target.value)} />
          </Field>
        )}
        {scope === "GUARDS" && query && (
          <ul className="plain stack" aria-label="Matching Guards">
            {(found.data ?? []).length === 0 ? (
              <li className="muted small">{found.loading ? "Searching…" : "No active Guard matches."}</li>
            ) : (
              (found.data ?? []).map((g) => (
                <li key={g.id} className="row">
                  <span>
                    {g.full_name} <span className="muted small mono">{g.display_id}</span>
                    {g.location ? <span className="muted small"> · {[g.location.building_name, g.location.floor_name, g.location.area_name].filter(Boolean).join(" › ")}</span> : <span className="muted small"> · Standby</span>}
                  </span>
                  <Button size="sm" variant="outline" disabled={guardIds.includes(g.display_id)} onClick={() => add(g.display_id)}>
                    {guardIds.includes(g.display_id) ? "Added" : "Add"}
                  </Button>
                </li>
              ))
            )}
          </ul>
        )}
        {scope === "GUARDS" && (
          <Field label="Guard IDs" required hint="Separate several IDs with commas or new lines.">
            <textarea value={guardText} onChange={(e) => setGuardText(e.target.value)} />
          </Field>
        )}
        <Field label="Note to the Guards">
          <textarea value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-foot">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy} disabled={incomplete}>
            Escalate
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
