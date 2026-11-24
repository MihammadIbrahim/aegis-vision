import { useState } from "react";

import { Field, FormDialog, PasswordInput } from "../../../components/ui";
import { recordings } from "../api";
import { UNIT_LABELS } from "../labels";
import type { Retention, RetentionUnit, StorageOverview } from "../types";

/** Admins and Supervisors (requirements Section 17): how long routine recordings are kept, in minutes,
 * hours or days, confirmed with the signed-in user's own password. Evidence is not affected. */
export function RetentionDialog({
  current,
  onClose,
  onSaved,
}: {
  current: Retention;
  onClose: () => void;
  onSaved: (overview: StorageOverview) => void;
}) {
  const [value, setValue] = useState(String(current.value));
  const [unit, setUnit] = useState<RetentionUnit>(current.unit);
  const [password, setPassword] = useState("");

  async function submit() {
    const n = Number(value);
    if (!Number.isInteger(n) || n < 1) throw new Error("Enter a whole number of at least 1.");
    if (!password) throw new Error("Your password is required to change the retention.");
    onSaved(await recordings.setRetention(n, unit, password));
  }

  return (
    <FormDialog title="Recording retention" submitLabel="Save retention" onClose={onClose} onSubmit={submit}>
      <p className="muted small">
        Routine recordings older than this are deleted automatically (1 minute to 365 days). Evidence in the vault is never
        deleted by retention.
      </p>
      <div className="grid-2">
        <Field label="Keep recordings for" required>
          <input type="number" inputMode="numeric" min={1} max={100000} value={value} onChange={(e) => setValue(e.target.value)} />
        </Field>
        <Field label="Unit" required>
          <select value={unit} onChange={(e) => setUnit(e.target.value as RetentionUnit)}>
            {(Object.keys(UNIT_LABELS) as RetentionUnit[]).map((u) => (
              <option key={u} value={u}>
                {UNIT_LABELS[u]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Your password" required hint="Confirms that you make this change; it is recorded in the audit log">
        <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
    </FormDialog>
  );
}
