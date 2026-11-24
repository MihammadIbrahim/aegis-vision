import { ShieldQuestion } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button, ErrorState } from "../../../components/ui";
import { access } from "../api";
import type { AccessCheck, RestrictedArea } from "../types";
import { areaLabel, DECISIONS, localToIso } from "./shared";

/** Is this already identified person allowed in this area at this time? (Admins and Supervisors) */
export function AccessCheckForm({ personnelId, areas }: { personnelId: string; areas: RestrictedArea[] }) {
  const [areaId, setAreaId] = useState("");
  const [at, setAt] = useState("");
  const [result, setResult] = useState<AccessCheck | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function check(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      setResult(await access.check(personnelId, areaId, localToIso(at) ?? undefined));
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="stack">
      <form className="row" onSubmit={check}>
        <select aria-label="Area to check" value={areaId} required onChange={(e) => setAreaId(e.target.value)}>
          <option value="">Check access to…</option>
          {areas.map((a) => (
            <option key={a.area_id} value={a.area_id}>
              {areaLabel(a)}
            </option>
          ))}
        </select>
        <input type="datetime-local" aria-label="At" value={at} onChange={(e) => setAt(e.target.value)} />
        <Button type="submit" variant="secondary" icon={ShieldQuestion}>
          Check
        </Button>
      </form>
      {error ? <ErrorState error={error} /> : null}
      {result && (
        <div className={`notice ${result.authorized ? "success" : "warning"}`} role="status">
          {DECISIONS[result.decision] ?? result.decision}
        </div>
      )}
    </div>
  );
}
