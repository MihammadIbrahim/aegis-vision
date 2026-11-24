import { RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Button, DataTable, ErrorState, PageHeader, StatusBadge, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { cameras } from "../../cameras";
import { incidents } from "../../incidents/api";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { evidence } from "../api";
import { formatBytes } from "../labels";
import type { EvidenceItem, EvidenceKind } from "../types";

export const SEARCH_DELAY_MS = 300;

/** The investigation evidence vault of this campus: clips and snapshots that never expire. */
export function EvidencePage() {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"" | EvidenceKind>("");
  const [camera, setCamera] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [tagText, setTagText] = useState("");
  const [tag, setTag] = useState("");
  const [place, setPlace] = useState<LocationValue>(NO_LOCATION);
  const [incident, setIncident] = useState("");
  const cameraList = useAsync(() => cameras.list(), []);
  const tree = useAsync(() => locations.tree(), []);
  const cases = useAsync(() => incidents.list({ limit: 100, offset: 0 }), []);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(text);
      setTag(tagText.trim());
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text, tagText]);
  const list = useAsync(
    () =>
      evidence.list({
        q: q.trim() || undefined,
        kind: kind || undefined,
        camera_id: camera || undefined,
        since: from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
        until: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
        tag: tag || undefined,
        building_id: place.building_id || undefined,
        floor_id: place.floor_id || undefined,
        area_id: place.area_id || undefined,
        incident_id: incident || undefined,
        limit: 200,
      }),
    [q, kind, camera, from, to, tag, place, incident],
  );

  const columns: Column<EvidenceItem>[] = [
    { key: "title", header: "Title", render: (e) => <Link to={`/evidence/${e.id}`}><strong>{e.title}</strong></Link> },
    { key: "kind", header: "Type", render: (e) => <StatusBadge status={e.kind} /> },
    {
      key: "camera",
      header: "Camera / location",
      render: (e) => (
        <div className="stack gap-0">
          <span>{e.camera_name}</span>
          {e.location && <span className="muted small">{e.location}</span>}
        </div>
      ),
    },
    { key: "covers", header: "Covers", render: (e) => formatDateTime(e.covers_from) },
    {
      key: "ref",
      header: "Case / investigation",
      render: (e) =>
        e.incident_id || e.investigation_ref ? (
          <div className="stack gap-0">
            {e.incident_id && <Link to={`/incidents/${e.incident_id}`}>{e.incident_reference ?? "Case"}</Link>}
            {e.investigation_ref && <span className="small">{e.investigation_ref}</span>}
          </div>
        ) : (
          <span className="muted">—</span>
        ),
    },
    {
      key: "tags",
      header: "Tags",
      render: (e) =>
        e.tags && e.tags.length > 0 ? (
          <span className="tag-list">
            {e.tags.map((t) => (
              <button key={t} type="button" className="tag" title={`Show items tagged "${t}"`} onClick={() => setTagText(t)}>
                {t}
              </button>
            ))}
          </span>
        ) : (
          <span className="muted">—</span>
        ),
    },
    {
      key: "source",
      header: "Saved from",
      render: (e) =>
        e.alert_id ? <Link to={`/alerts/${e.alert_id}`}>Alert</Link> : e.source === "PERSON_SEARCH" ? "Person search" : "Playback",
    },
    { key: "by", header: "Saved by", render: (e) => `${e.saved_by_name} · ${formatDateTime(e.saved_at)}` },
    { key: "size", header: "Size", align: "right", render: (e) => formatBytes(e.size_bytes) },
  ];
  const filtered = Boolean(q || kind || camera || from || to || tag || place.building_id || incident);
  return (
    <section>
      <PageHeader
        title="Evidence Vault"
        description="Clips and snapshots kept for investigations. They never expire with routine recordings; only an Admin can delete them, with password confirmation."
        actions={
          <Button variant="secondary" icon={RotateCw} onClick={list.reload} loading={list.loading && Boolean(list.data)}>
            Refresh
          </Button>
        }
      />
      <div className="filters" role="search" aria-label="Find evidence">
        <input type="search" aria-label="Search" placeholder="Title, notes, investigation reference or camera" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
        <select aria-label="Type" value={kind} onChange={(e) => setKind(e.target.value as "" | EvidenceKind)}>
          <option value="">All types</option>
          <option value="CLIP">Video clips</option>
          <option value="SNAPSHOT">Snapshots</option>
        </select>
        <select aria-label="Camera" value={camera} onChange={(e) => setCamera(e.target.value)}>
          <option value="">All cameras</option>
          {(cameraList.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.display_name}
            </option>
          ))}
        </select>
        <input type="search" aria-label="Tag" placeholder="Tag" value={tagText} maxLength={40} onChange={(e) => setTagText(e.target.value)} />
        <LocationPicker tree={tree.data ?? []} value={place} onChange={setPlace} idPrefix="evidence" />
        <select aria-label="Case" value={incident} onChange={(e) => setIncident(e.target.value)}>
          <option value="">Any case</option>
          {(cases.data?.items ?? []).map((i) => (
            <option key={i.id} value={i.id}>
              {i.reference} · {i.title}
            </option>
          ))}
        </select>
        <label className="small">
          From <input type="date" aria-label="From date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label className="small">
          To <input type="date" aria-label="To date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
      </div>
      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : (
        <>
          <DataTable caption="Evidence" columns={columns} rows={list.data ?? []} rowKey={(e) => e.id} loading={list.loading && !list.data} empty={filtered ? "No evidence matches." : "No evidence saved yet. Save clips from an alert or from Recordings."} />
          {list.data && (
            <p className="muted small" aria-live="polite">
              {list.data.length} item{list.data.length === 1 ? "" : "s"}.
            </p>
          )}
        </>
      )}
    </section>
  );
}
