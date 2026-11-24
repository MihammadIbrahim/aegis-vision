import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button, ErrorState } from "../../../components/ui";
import { personnel } from "../api";
import type { DirectoryRow } from "../types";

/** Search the campus directory and pick one person. Uses the same directory endpoint as the
 * Personnel Directory, so a Supervisor sees basic columns only. */
export function PersonnelSelector({ onSelect, label = "Find personnel" }: { onSelect: (person: DirectoryRow) => void; label?: string }) {
  const [text, setText] = useState("");
  const [results, setResults] = useState<DirectoryRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function find(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResults((await personnel.directory({ q: text, limit: 10 })).items);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <form className="row selector-form" onSubmit={find} role="search">
        <input aria-label={label} placeholder="Name, Personnel ID or Institutional ID" value={text} onChange={(e) => setText(e.target.value)} />
        <Button type="submit" icon={Search} loading={busy}>
          Search
        </Button>
      </form>
      {error ? <ErrorState error={error} /> : null}
      {results && results.length === 0 && <p className="muted">No personnel found.</p>}
      {results && results.length > 0 && (
        <ul className="plain selector-results" aria-label="Search results">
          {results.map((r) => (
            <li key={r.id}>
              <button type="button" className="link" onClick={() => onSelect(r)}>
                {r.full_name} · {r.personnel_id} · {r.category.label}
                {r.department ? ` · ${r.department}` : ""}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
