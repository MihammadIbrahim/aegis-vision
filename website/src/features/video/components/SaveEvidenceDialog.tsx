import { useState } from "react";

import { Field, FormDialog } from "../../../components/ui";
import { evidence } from "../api";
import { CaseSelect, parseTags, TagsField, tagsProblem } from "./EvidenceFields";
import type { EvidenceItem, EvidenceKind } from "../types";

export type EvidenceOrigin = { alertId: string } | { segmentId: string; startOffset: number; endOffset: number | null };

/** Saves a clip or snapshot into the evidence vault from an alert or from playback. */
export function SaveEvidenceDialog({
  origin,
  defaultTitle,
  onClose,
  onSaved,
}: {
  origin: EvidenceOrigin;
  defaultTitle: string;
  onClose: () => void;
  onSaved: (item: EvidenceItem) => void;
}) {
  const fromPlayback = "segmentId" in origin;
  const clipPossible = !fromPlayback || origin.endOffset !== null;
  const [kind, setKind] = useState<EvidenceKind>(clipPossible ? "CLIP" : "SNAPSHOT");
  const [title, setTitle] = useState(defaultTitle);
  const [ref, setRef] = useState("");
  const [notes, setNotes] = useState("");
  const [tags, setTags] = useState("");
  const [incident, setIncident] = useState("");

  async function submit() {
    const details = {
      title: title.trim(),
      notes: notes.trim() || null,
      investigation_ref: ref.trim() || null,
      tags: parseTags(tags),
      incident_id: incident || null,
    };
    if (!details.title) throw new Error("A title is required.");
    const item = fromPlayback
      ? await evidence.fromPlayback(origin.segmentId, kind, origin.startOffset, kind === "CLIP" ? origin.endOffset : null, details)
      : await evidence.fromAlert(origin.alertId, kind, details);
    onSaved(item);
  }

  return (
    <FormDialog title="Save to evidence vault" submitLabel="Save evidence" disabled={!title.trim() || tagsProblem(tags) !== null} onClose={onClose} onSubmit={submit}>
      <p className="muted small">
        Evidence never expires with routine recordings. Saving, viewing, changing and deleting it is recorded in the audit log.
      </p>
      <Field label="Type">
        <select value={kind} onChange={(e) => setKind(e.target.value as EvidenceKind)}>
          {clipPossible && <option value="CLIP">Video clip</option>}
          <option value="SNAPSHOT">Snapshot (one frame)</option>
        </select>
      </Field>
      <Field label="Title" required>
        <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Investigation reference" hint="Case or incident number, if any">
        <input value={ref} maxLength={100} onChange={(e) => setRef(e.target.value)} />
      </Field>
      <TagsField value={tags} onChange={setTags} />
      <CaseSelect value={incident} onChange={setIncident} />
      <Field label="Notes">
        <textarea value={notes} maxLength={2000} rows={3} onChange={(e) => setNotes(e.target.value)} />
      </Field>
    </FormDialog>
  );
}
