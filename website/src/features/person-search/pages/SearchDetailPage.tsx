import { ArrowLeft, Ban, CircleCheck, Link2, Save, Undo2, Video } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AuthMedia } from "../../../components/AuthMedia";
import { Button, Dialog, ErrorState, Field, FormDialog, LoadingState, PageHeader, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { incidents } from "../../incidents/api";
import { parseTags, TagsField, tagsProblem } from "../../video/components/EvidenceFields";
import { LIMITATION_TEXT, personSearch, type Candidate, type Review } from "../api";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function ResultCard({ c, onChanged }: { c: Candidate; onChanged: () => void }) {
  const toast = useToast();
  const [dialog, setDialog] = useState<"link" | "evidence" | "clip" | null>(null);
  const [incident, setIncident] = useState("");
  const [title, setTitle] = useState(`Person-search result · ${c.camera_name}`);
  const [notes, setNotes] = useState("");
  const [tags, setTags] = useState("person-search");
  const open = useAsync(
    () => (dialog === "link" ? incidents.list({ status: ["OPEN", "UNDER_INVESTIGATION", "RESPONDING"], limit: 100, offset: 0 }) : Promise.resolve(null)),
    [dialog],
  );
  async function review(value: Review) {
    try {
      await personSearch.review(c.id, value, null);
      onChanged();
    } catch (e) {
      toast("error", message(e));
    }
  }
  return (
    <li className="crop-card">
      {c.has_crop ? <AuthMedia path={personSearch.paths.resultCrop(c.id)} alt={`Possible match on ${c.camera_name}`} className="crop" /> : <div className="crop muted small">No picture</div>}
      <span className="row">
        <StatusBadge status={c.band} /> <span className="small tabular-nums">similarity {(Math.floor(c.similarity * 100) / 100).toFixed(2)}</span>
      </span>
      <span className="small">
        {c.camera_name}
        {c.location ? ` · ${c.location}` : ""}
      </span>
      <span className="muted small">{formatDateTime(c.observed_at)}</span>
      <span className="row small">
        <StatusBadge status={c.review} />
        {c.reviewed_by_name && <span className="muted">by {c.reviewed_by_name}</span>}
      </span>
      {c.incident_reference && (
        <Link className="small" to={`/incidents/${c.incident_id}`}>
          Linked to {c.incident_reference}
        </Link>
      )}
      {c.evidence_id && (
        <Link className="small" to={`/evidence/${c.evidence_id}`}>
          Saved as evidence
        </Link>
      )}
      <div className="actions">
        {c.review === "PENDING" ? (
          <>
            <Button size="sm" icon={CircleCheck} disabled={c.band !== "LIKELY_MATCH"} title={c.band !== "LIKELY_MATCH" ? "An uncertain result cannot be verified" : undefined} onClick={() => void review("VERIFIED_MATCH")}>
              Verified match
            </Button>
            <Button size="sm" variant="secondary" icon={Ban} onClick={() => void review("NOT_A_MATCH")}>
              Not a match
            </Button>
          </>
        ) : (
          !c.incident_id && (
            <Button size="sm" variant="ghost" icon={Undo2} onClick={() => void review("PENDING")}>
              Undo review
            </Button>
          )
        )}
        {c.review === "VERIFIED_MATCH" && !c.incident_id && (
          <Button size="sm" variant="secondary" icon={Link2} onClick={() => setDialog("link")}>
            Link to case
          </Button>
        )}
        {!c.evidence_id && c.has_crop && (
          <Button size="sm" variant="secondary" icon={Save} onClick={() => setDialog("evidence")}>
            Save as evidence
          </Button>
        )}
        {c.camera_id && (
          <Button size="sm" variant="ghost" icon={Video} onClick={() => setDialog("clip")}>
            Clip
          </Button>
        )}
      </div>
      {dialog === "link" && (
        <FormDialog
          title="Link the verified result to a case"
          submitLabel="Link"
          disabled={!incident}
          onClose={() => setDialog(null)}
          onSubmit={async () => {
            await personSearch.link(c.id, incident);
            toast("success", "Linked to the case.");
            setDialog(null);
            onChanged();
          }}
        >
          <Field label="Open case" required>
            <select value={incident} onChange={(e) => setIncident(e.target.value)}>
              <option value="">Choose a case</option>
              {(open.data?.items ?? []).map((i) => (
                <option key={i.id} value={i.id}>
                  {i.reference} · {i.title}
                </option>
              ))}
            </select>
          </Field>
        </FormDialog>
      )}
      {dialog === "evidence" && (
        <FormDialog
          title="Save the result's picture as evidence"
          submitLabel="Save"
          disabled={!title.trim() || tagsProblem(tags) !== null}
          onClose={() => setDialog(null)}
          onSubmit={async () => {
            await personSearch.saveEvidence(c.id, title.trim(), notes.trim() || null, parseTags(tags));
            toast("success", "Saved to the evidence vault.");
            setDialog(null);
            onChanged();
          }}
        >
          <Field label="Title" required>
            <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <TagsField value={tags} onChange={setTags} />
          <Field label="Notes">
            <textarea value={notes} maxLength={2000} onChange={(e) => setNotes(e.target.value)} />
          </Field>
          {c.incident_reference && <p className="muted small">The saved item is linked to {c.incident_reference}.</p>}
        </FormDialog>
      )}
      {dialog === "clip" && (
        <Dialog open title={`Recording around the result · ${c.camera_name}`} onClose={() => setDialog(null)}>
          <AuthMedia path={personSearch.paths.resultClip(c.id)} alt="Recording around the result" kind="video" className="player-video" fallback="No recording is kept for this moment." />
        </Dialog>
      )}
    </li>
  );
}

/** One search: the reference, what was searched, the limits of the footage, and the results to review. */
export function SearchDetailPage() {
  const { id = "" } = useParams();
  const search = useAsync(() => personSearch.get(id), [id]);
  if (search.error) return <ErrorState error={search.error} onRetry={search.reload} />;
  if (!search.data) return <LoadingState />;
  const s = search.data;
  return (
    <section>
      <Link to="/person-search" className="back-link">
        <ArrowLeft className="icon" aria-hidden="true" /> Person Search
      </Link>
      <PageHeader
        title={`Search of ${formatDateTime(s.created_at)}`}
        description={`${s.kind === "PHOTO" ? "By reference photo" : "By an observed person"} · ${formatDateTime(s.period_from)} to ${formatDateTime(s.period_to)} · ${s.searched_observations} observations compared · by ${s.created_by_name}`}
      />
      <div className="grid-2">
        <section className="card" aria-labelledby="ps-reference">
          <h2 id="ps-reference">Reference</h2>
          {s.has_reference ? <AuthMedia path={personSearch.paths.reference(s.id)} alt="Reference" className="crop" /> : <p className="muted">No picture kept.</p>}
          {s.note && <p className="small prewrap">{s.note}</p>}
        </section>
        <section className="card" aria-labelledby="ps-limits">
          <h2 id="ps-limits">Coverage</h2>
          {s.complete ? (
            <p>Every camera in scope was online, ran Re-ID and observed people in the period.</p>
          ) : (
            <>
              <p className="notice warning" role="note">This search is not complete: some footage could not be searched.</p>
              <ul className="plain stack small">
                {s.limitations.map((x, n) => (
                  <li key={`${x.camera_name}-${x.issue}-${n}`}>
                    <strong>{x.camera_name}</strong>: {LIMITATION_TEXT[x.issue]}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>
      <section className="card" aria-labelledby="ps-results">
        <h2 id="ps-results">Possible matches ({s.candidates.length})</h2>
        <p className="muted small">
          Ordered by similarity of appearance. “Likely match” and “Uncertain” describe similarity only; a reviewer decides, and even a verified
          match is not proof of identity.
        </p>
        {s.candidates.length === 0 ? (
          <p className="muted">No observation was similar enough.</p>
        ) : (
          <ul className="crop-grid" aria-label="Possible matches">
            {s.candidates.map((c) => (
              <ResultCard key={c.id} c={c} onChanged={search.reload} />
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
