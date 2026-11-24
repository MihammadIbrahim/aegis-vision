import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button, ErrorState, useToast } from "../../../components/ui";
import type { PersonnelBasic } from "../../personnel";
import { access, type GrantTerms } from "../api";
import type { RestrictedArea } from "../types";
import { areaLabel, DAYS, localToIso } from "./shared";

/** Admins grant one person access to one or more restricted areas at once (all or none). */
export function GrantForm({ person, areas, onGranted }: { person: PersonnelBasic; areas: RestrictedArea[]; onGranted: () => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [useSchedule, setUseSchedule] = useState(false);
  const [days, setDays] = useState<number[]>([1, 2, 3, 4, 5]);
  const [start, setStart] = useState("08:00");
  const [end, setEnd] = useState("18:00");
  const [timezone, setTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const terms: GrantTerms = {
      valid_from: localToIso(from),
      valid_until: localToIso(until),
      schedule: useSchedule ? { days, start, end, timezone } : null,
      note: note || null,
    };
    setBusy(true);
    try {
      await access.grant(person.id, selected, terms);
      setSelected([]);
      toast("success", "Access granted.");
      onGranted();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <h3>Grant access to {person.full_name}</h3>
      {error ? <ErrorState error={error} /> : null}
      {person.status !== "ACTIVE" && <div className="notice warning">This person is inactive; access cannot be granted.</div>}
      <fieldset>
        <legend>Restricted areas</legend>
        {areas.map((a) => (
          <label key={a.area_id} className="checkbox">
            <input
              type="checkbox"
              checked={selected.includes(a.area_id)}
              onChange={(e) =>
                setSelected(e.target.checked ? [...selected, a.area_id] : selected.filter((x) => x !== a.area_id))
              }
            />
            {areaLabel(a)}
          </label>
        ))}
        {areas.length === 0 && <p className="muted">No restricted areas yet. Mark areas as restricted first.</p>}
      </fieldset>
      <div className="row">
        <label>
          Valid from (empty = now)
          <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          Valid until (empty = until revoked)
          <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={useSchedule} onChange={(e) => setUseSchedule(e.target.checked)} />
        Only on certain days and hours
      </label>
      {useSchedule && (
        <div className="stack">
          <div className="row">
            {DAYS.map((label, index) => (
              <label key={label} className="checkbox">
                <input
                  type="checkbox"
                  checked={days.includes(index + 1)}
                  onChange={(e) =>
                    setDays(e.target.checked ? [...days, index + 1].sort() : days.filter((d) => d !== index + 1))
                  }
                />
                {label}
              </label>
            ))}
          </div>
          <div className="row">
            <label>
              From
              <input type="time" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label>
              To
              <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
            <label>
              Time zone
              <input value={timezone} onChange={(e) => setTimezone(e.target.value)} />
            </label>
          </div>
        </div>
      )}
      <label>
        Note (Admins only)
        <input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div>
        <Button type="submit" icon={KeyRound} loading={busy} disabled={selected.length === 0 || person.status !== "ACTIVE"}>
          Grant access to {selected.length} area(s)
        </Button>
      </div>
    </form>
  );
}
