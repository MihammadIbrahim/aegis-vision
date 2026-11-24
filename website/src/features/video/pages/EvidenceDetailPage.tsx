import { Download, Link2, Save, Trash2, Unlink } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useAuth } from "../../../auth/AuthContext";
import { Button, ErrorState, Field, LoadingState, PageHeader, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatDateTime } from "../../../lib/format";
import { evidence } from "../api";
import { incidents } from "../../incidents/api";
import { DeleteEvidenceDialog } from "../components/DeleteEvidenceDialog";
import { CaseSelect, parseTags, TagsField, tagsProblem } from "../components/EvidenceFields";
import { formatBytes } from "../labels";
import type { EvidenceItem } from "../types";

/** The media is fetched with the session token and shown from an object URL (each fetch is an
 * audited view). */
function EvidenceViewer({ item }: { item: EvidenceItem }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    evidence
      .media(item.id)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((e: unknown) => active && setError(e));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.id]);
  if (error) return <ErrorState error={error} />;
  if (!url) return <LoadingState label="Loading evidence…" />;
  const name = `evidence-${item.id}.${item.kind === "CLIP" ? "mp4" : "jpg"}`;
  return (
    <div className="stack">
      {item.kind === "CLIP" ? <video src={url} controls playsInline className="player-video" aria-label={`Evidence clip: ${item.title}`} /> : <img src={url} alt={`Evidence snapshot: ${item.title}`} className="player-video" />}
      <div className="actions">
        <a className="btn btn-outline btn-sm" href={url} download={name}>
          <Download className="icon" aria-hidden="true" /> Download
        </a>
        <span className="muted small mono">SHA-256 {item.sha256.slice(0, 16)}…</span>
      </div>
    </div>
  );
}

export function EvidenceDetailPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const admin = session?.role === "ADMIN";
  const item = useAsync(() => evidence.get(id), [id]);
  const [title, setTitle] = useState("");
  const [ref, setRef] = useState("");
  const [notes, setNotes] = useState("");
  const [tags, setTags] = useState("");
  const [caseId, setCaseId] = useState("");
  const [linking, setLinking] = useState(false);
  const { confirm, dialog } = useConfirm();
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    if (item.data) {
      setTitle(item.data.title);
      setRef(item.data.investigation_ref ?? "");
      setNotes(item.data.notes);
      setTags((item.data.tags ?? []).join(", "));
    }
  }, [item.data]);

  if (!item.data) return item.error ? <ErrorState error={item.error} onRetry={item.reload} /> : <LoadingState />;
  const e = item.data;
  const savedTags = e.tags ?? [];
  const tagsChanged = parseTags(tags).join("\u0000") !== savedTags.join("\u0000");
  const dirty = title !== e.title || ref !== (e.investigation_ref ?? "") || notes !== e.notes || tagsChanged;
  const tagError = tagsProblem(tags);

  async function linkCase() {
    if (!caseId) return;
    setLinking(true);
    try {
      await incidents.link(caseId, "evidence", e.id);
      toast("success", "Linked to the case.");
      setCaseId("");
      item.reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : String(err));
    } finally {
      setLinking(false);
    }
  }

  async function unlinkCase() {
    if (!e.incident_id) return;
    const answer = await confirm({
      title: "Remove from the case?",
      message: `The item stays in the vault; it is no longer part of ${e.incident_reference ?? "the case"}. The case history records the change.`,
      confirmLabel: "Remove from case",
      tone: "danger",
    });
    if (!answer.confirmed) return;
    try {
      await incidents.unlink(e.incident_id, "evidence", e.id);
      toast("success", "Removed from the case.");
      item.reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : String(err));
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await evidence.update(e.id, { title: title.trim(), notes: notes.trim() || null, investigation_ref: ref.trim() || null, tags: parseTags(tags) });
      toast("success", "Evidence details saved.");
      item.reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: "/evidence", label: "Evidence Vault" }}
        title={
          <>
            <StatusBadge status={e.kind} /> {e.title}
          </>
        }
        description={`${e.camera_name} · covers ${formatDateTime(e.covers_from)}${e.kind === "CLIP" ? ` to ${formatDateTime(e.covers_to)}` : ""}`}
        actions={
          admin && (
            <Button variant="danger" icon={Trash2} onClick={() => setDeleting(true)}>
              Delete evidence
            </Button>
          )
        }
      />
      <div className="grid-2">
        <section className="card" aria-label="Evidence media">
          <EvidenceViewer item={e} />
          <dl className="detail-list">
            <dt>Saved by</dt>
            <dd>
              {e.saved_by_name} · {formatDateTime(e.saved_at)}
            </dd>
            <dt>Saved from</dt>
            <dd>
              {e.alert_id ? (
                <Link to={`/alerts/${e.alert_id}`}>Alert {e.alert_id.slice(0, 8)}…</Link>
              ) : e.source === "PERSON_SEARCH" ? (
                "Person search result"
              ) : (
                "Recording playback"
              )}
            </dd>
            <dt>Location when saved</dt>
            <dd>{e.location ?? "—"}</dd>
            <dt>Case</dt>
            <dd>
              {e.incident_id ? (
                <span className="row">
                  <Link to={`/incidents/${e.incident_id}`}>{e.incident_reference ?? "Open the case"}</Link>
                  <Button size="sm" variant="ghost" icon={Unlink} onClick={() => void unlinkCase()}>
                    Remove
                  </Button>
                </span>
              ) : (
                <span className="muted">Not part of a case</span>
              )}
            </dd>
            <dt>File</dt>
            <dd>
              {e.media_type} · {formatBytes(e.size_bytes)}
            </dd>
            <dt>Evidence ID</dt>
            <dd className="mono">{e.id}</dd>
          </dl>
        </section>
        <form className="card stack" onSubmit={save} aria-label="Evidence details">
          <h3>Details</h3>
          <Field label="Title" required>
            <input value={title} maxLength={200} onChange={(ev) => setTitle(ev.target.value)} />
          </Field>
          <Field label="Investigation reference" hint="Links this item to a case or incident">
            <input value={ref} maxLength={100} onChange={(ev) => setRef(ev.target.value)} />
          </Field>
          <TagsField value={tags} onChange={setTags} />
          <Field label="Notes">
            <textarea value={notes} rows={5} maxLength={2000} onChange={(ev) => setNotes(ev.target.value)} />
          </Field>
          <div className="actions">
            <Button type="submit" icon={Save} loading={saving} disabled={!dirty || !title.trim() || tagError !== null}>
              Save details
            </Button>
            <span className="muted small">Last updated {formatDateTime(e.updated_at)}</span>
          </div>
        </form>
      </div>
      {!e.incident_id && (
        <section className="card" aria-labelledby="evidence-case">
          <h3 id="evidence-case">Add to a case</h3>
          <div className="row">
            <CaseSelect value={caseId} onChange={setCaseId} label="Open case" />
            <Button icon={Link2} loading={linking} disabled={!caseId} onClick={() => void linkCase()}>
              Link to case
            </Button>
          </div>
        </section>
      )}
      {dialog}
      {deleting && (
        <DeleteEvidenceDialog
          item={e}
          onClose={() => setDeleting(false)}
          onDeleted={() => {
            toast("success", `"${e.title}" was deleted.`);
            navigate("/evidence");
          }}
        />
      )}
    </section>
  );
}
