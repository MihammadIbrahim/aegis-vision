import { useState } from "react";

import { imports } from "../api";
import type { ImportDetail } from "../types";
import { ListChecks } from "lucide-react";

import { Button, ErrorState } from "../../../components/ui";

const CONFIDENCE: Record<string, string> = {
  PROFILE: "from a saved mapping",
  EXACT: "exact match",
  SYNONYM: "known name",
  FUZZY: "similar name — check",
  NONE: "choose",
};

export function MappingStep({ detail, onValidated }: { detail: ImportDetail; onValidated: (d: ImportDetail) => void }) {
  const [targets, setTargets] = useState<Record<number, string>>(() =>
    Object.fromEntries(detail.columns.filter((c) => c.target).map((c) => [c.index, c.target as string])),
  );
  const [profile, setProfile] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const undecided = detail.columns.filter((c) => !targets[c.index]);
  const used = Object.values(targets).filter((t) => t !== "IGNORE");
  const missingRequired = detail.targets.filter((t) => t.required && !used.includes(t.target));

  async function validate() {
    setBusy(true);
    setError(null);
    try {
      const columns = detail.columns.map((c) => ({ index: c.index, target: targets[c.index] }));
      onValidated(await imports.map(detail.batch.id, columns, profile.trim() || null));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card stack">
      <h2>3. Match the columns to personnel fields</h2>
      <p className="muted">
        {detail.batch.original_filename}: review every suggestion. Columns you do not want must be set to "Do not import"; nothing
        is dropped silently. Organization and campus always come from your session.
      </p>
      {error ? <ErrorState error={error} /> : null}
      <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Column in your file</th>
            <th>Personnel field</th>
            <th>Suggestion</th>
          </tr>
        </thead>
        <tbody>
          {detail.columns.map((c) => (
            <tr key={c.index} className={targets[c.index] ? "" : "needs-decision"}>
              <td>{c.header}</td>
              <td>
                <select
                  aria-label={`Field for ${c.header}`}
                  value={targets[c.index] ?? ""}
                  onChange={(e) => setTargets({ ...targets, [c.index]: e.target.value })}
                >
                  <option value="">— choose —</option>
                  {detail.targets.map((t) => (
                    <option
                      key={t.target}
                      value={t.target}
                      disabled={t.target !== "IGNORE" && used.includes(t.target) && targets[c.index] !== t.target}
                    >
                      {t.label}
                      {t.required ? " *" : ""}
                    </option>
                  ))}
                </select>
              </td>
              <td className={`small confidence-${c.confidence.toLowerCase()}`}>{CONFIDENCE[c.confidence]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {missingRequired.length > 0 && (
        <p className="field-error">Required fields not mapped: {missingRequired.map((t) => t.label).join(", ")}</p>
      )}
      <label>
        Save this mapping for later files (optional name)
        <input value={profile} onChange={(e) => setProfile(e.target.value)} placeholder="e.g. Registrar export" />
      </label>
      <div>
        <Button icon={ListChecks} onClick={validate} loading={busy} disabled={undecided.length > 0 || missingRequired.length > 0}>
          Confirm mapping and validate
        </Button>
      </div>
      {undecided.length > 0 && <p className="muted small">{undecided.length} column(s) still need a decision.</p>}
    </div>
  );
}
