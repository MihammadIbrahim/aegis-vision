import { ImageUp, RotateCw, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AuthMedia } from "../../../components/AuthMedia";
import { Button, DataTable, ErrorState, Field, LoadingState, PageHeader, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { cameras } from "../../cameras";
import { personSearch, type Scope } from "../api";

function toIso(local: string): string | undefined {
  return local ? new Date(local).toISOString() : undefined;
}

/** Person Search (Org Admin page 7, Supervisor page 7): search by a reference photo or by a person a camera
 * observed, over a period and cameras. Results are resemblances that people review; never an identity. */
export function PersonSearchPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const model = useAsync(() => personSearch.model(), []);
  const cameraList = useAsync(() => cameras.list(), []);
  const [camera, setCamera] = useState("");
  const observations = useAsync(() => personSearch.observations({ camera_id: camera || undefined, limit: 24 }), [camera]);
  const searches = useAsync(() => personSearch.list(), []);
  const [photo, setPhoto] = useState<File | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [scopeCameras, setScopeCameras] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const scope = (): Scope => ({ since: toIso(from), until: toIso(to), camera_ids: scopeCameras, note: note.trim() || undefined });

  async function run(key: string, action: () => Promise<{ id: string }>) {
    setBusy(key);
    try {
      const result = await action();
      navigate(`/person-search/${result.id}`);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  function submitPhoto(event: FormEvent) {
    event.preventDefault();
    if (photo) void run("photo", () => personSearch.byPhoto(photo, scope()));
  }

  return (
    <section>
      <PageHeader
        title="Person Search"
        description="Find where a person may have appeared on this campus's cameras. Results are possible matches for your review, never a confirmed identity."
        actions={
          <Button variant="secondary" icon={RotateCw} onClick={() => { observations.reload(); searches.reload(); }}>
            Refresh
          </Button>
        }
      />
      <section className="card" aria-labelledby="ps-scope">
        <h2 id="ps-scope">Search where and when</h2>
        <div className="form-grid">
          <Field label="From" hint="Default: the last 24 hours (at most 31 days)">
            <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="To">
            <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
          </Field>
          <Field label="Cameras" hint="None selected: every camera of the campus">
            <select multiple value={scopeCameras} onChange={(e) => setScopeCameras(Array.from(e.target.selectedOptions, (o) => o.value))}>
              {(cameraList.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Note (kept with the search)">
            <input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
      </section>
      <div className="grid-2">
        <section className="card" aria-labelledby="ps-photo">
          <h2 id="ps-photo">By reference photo</h2>
          {model.data && !model.data.photo_search_available ? (
            <p className="notice warning" role="note">
              Photo search needs the trained Re-ID model on the server, which is not installed yet ({model.data.reason}). Search by an observed
              person instead.
            </p>
          ) : (
            <form className="stack" onSubmit={submitPhoto}>
              <Field label="Photo of the person" required hint="JPEG or PNG, up to 8 MB; a full-body picture works best.">
                <input type="file" accept="image/jpeg,image/png" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
              </Field>
              <div className="actions">
                <Button type="submit" icon={ImageUp} loading={busy === "photo"} disabled={!photo}>
                  Search with this photo
                </Button>
              </div>
            </form>
          )}
        </section>
        <section className="card" aria-labelledby="ps-observed">
          <div className="card-head">
            <h2 id="ps-observed">By an observed person</h2>
            <select aria-label="Camera of the observations" value={camera} onChange={(e) => setCamera(e.target.value)}>
              <option value="">All cameras</option>
              {(cameraList.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.display_name}
                </option>
              ))}
            </select>
          </div>
          {observations.error ? (
            <ErrorState error={observations.error} onRetry={observations.reload} />
          ) : !observations.data ? (
            <LoadingState />
          ) : observations.data.length === 0 ? (
            <p className="muted">No person has been observed recently. Observations come from cameras running Re-ID.</p>
          ) : (
            <ul className="crop-grid" aria-label="Recently observed people">
              {observations.data.map((o) => (
                <li key={o.id} className="crop-card">
                  {o.has_crop ? <AuthMedia path={personSearch.paths.observationCrop(o.id)} alt={`Person on ${o.camera_name}`} className="crop" /> : <div className="crop muted small">No picture</div>}
                  <span className="small">{o.camera_name}</span>
                  <span className="muted small">{formatDateTime(o.observed_at)}</span>
                  <Button size="sm" variant="secondary" icon={Search} loading={busy === o.id} onClick={() => void run(o.id, () => personSearch.byObservation(o.id, scope()))}>
                    Find this person
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <section className="card" aria-labelledby="ps-history">
        <h2 id="ps-history">Earlier searches</h2>
        {searches.error ? (
          <ErrorState error={searches.error} onRetry={searches.reload} />
        ) : (
          <DataTable
            caption="Earlier searches"
            columns={[
              { key: "when", header: "Searched", render: (s) => <Link to={`/person-search/${s.id}`}>{formatDateTime(s.created_at)}</Link> },
              { key: "kind", header: "By", render: (s) => (s.kind === "PHOTO" ? "Photo" : "Observed person") },
              { key: "period", header: "Period", render: (s) => `${formatDateTime(s.period_from)} – ${formatDateTime(s.period_to)}` },
              { key: "results", header: "Results", align: "right", render: (s) => s.candidate_count },
              { key: "complete", header: "Coverage", render: (s) => (s.complete ? "Complete" : `${s.limitations.length} limitation${s.limitations.length === 1 ? "" : "s"}`) },
              { key: "by", header: "Searched by", render: (s) => s.created_by_name },
            ]}
            rows={searches.data ?? []}
            rowKey={(s) => s.id}
            loading={searches.loading && !searches.data}
            empty="No searches yet."
          />
        )}
      </section>
    </section>
  );
}
