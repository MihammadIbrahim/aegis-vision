import { HardDrive, RotateCw, Settings2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { Button, DataTable, ErrorState, PageHeader, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { recordings } from "../api";
import { RetentionDialog } from "../components/RetentionDialog";
import { SegmentPlayer } from "../components/SegmentPlayer";
import { TimelineBar } from "../components/TimelineBar";
import { formatBytes, formatCountdown, formatDuration, formatRetention, formatSeconds } from "../labels";
import type { RecordingSegment, StorageOverview } from "../types";

export function StoragePanel({ overview, onChange }: { overview: StorageOverview; onChange: () => void }) {
  const s = overview.storage;
  const u = overview.usage;
  return (
    <section className="card" aria-labelledby="storage-title">
      <div className="card-head">
        <h3 id="storage-title">
          <HardDrive className="icon" aria-hidden="true" /> Recording storage
        </h3>
        <Button variant="outline" size="sm" icon={Settings2} onClick={onChange}>
          Change retention
        </Button>
      </div>
      <dl className="detail-list">
        <dt>Routine recordings kept for</dt>
        <dd>
          {formatRetention(overview.retention)}
          {overview.retention.is_default ? <span className="muted small"> (server default)</span> : overview.retention.updated_by_name ? <span className="muted small"> · set by {overview.retention.updated_by_name} {formatDateTime(overview.retention.updated_at)}</span> : null}
        </dd>
        <dt>Routine recordings (this campus)</dt>
        <dd>
          {u.recording_segments} file{u.recording_segments === 1 ? "" : "s"} · {formatBytes(u.recording_bytes)}
          {u.oldest_recording_at && <span className="muted small"> · oldest {formatDateTime(u.oldest_recording_at)}</span>}
        </dd>
        <dt>Evidence vault (this campus)</dt>
        <dd>
          {u.evidence_items} item{u.evidence_items === 1 ? "" : "s"} · {formatBytes(u.evidence_bytes)}
          <span className="muted small"> · never expires; deleted only by an Admin with password confirmation</span>
        </dd>
        <dt>Storage provider</dt>
        <dd>
          {overview.provider === "LOCAL_DISK" ? "Local disk on the storage server" : overview.provider}
          {s ? <span className="muted small"> · last checked {formatDateTime(s.reported_at)}</span> : null}
        </dd>
        <dt>Disk use</dt>
        <dd>
          {s ? (
            <>
              <StatusBadge status={s.level === "OK" ? "ACTIVE" : s.level === "WARNING" ? "RESTRICTED" : "FAILED"} label={s.level === "OK" ? "Healthy" : s.level === "WARNING" ? "Low storage" : "Critical"} /> {s.percent_used}% used · {formatBytes(s.free_bytes)} free of {formatBytes(s.total_bytes)} · reported {formatDateTime(s.reported_at)}
              <span className="muted small"> (warning at {overview.warning_percent}%, critical at {overview.critical_percent}%)</span>
            </>
          ) : (
            <span className="muted">Not reported yet (the retention daemon has not run).</span>
          )}
        </dd>
      </dl>
    </section>
  );
}

/** Recordings archive: timeline with AI event markers, camera and period filters, playback and
 * saving evidence; retention and disk use. Every playback is audited by the server. */
export function RecordingsPage() {
  const toast = useToast();
  const tree = useAsync(() => locations.tree(), []);
  const [location, setLocation] = useState<LocationValue>(NO_LOCATION);
  const [camera, setCamera] = useState("");
  // Empty = the server's default period, "the last 24 hours up to now", evaluated on every load, so
  // Refresh always shows the newest segments (a period fixed when the page opened would hide them).
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [selected, setSelected] = useState<RecordingSegment | null>(null);
  const [editingRetention, setEditingRetention] = useState(false);
  const query = useMemo(
    () => ({
      camera_id: camera || undefined,
      building_id: location.building_id || undefined,
      floor_id: location.floor_id || undefined,
      area_id: location.area_id || undefined,
      since: since ? new Date(since).toISOString() : undefined,
      until: until ? new Date(until).toISOString() : undefined,
      limit: 500,
    }),
    [camera, location, since, until],
  );
  const timeline = useAsync(() => recordings.timeline(query), [query]);
  const storage = useAsync(() => recordings.retention(), []);

  const columns: Column<RecordingSegment>[] = [
    { key: "camera", header: "Camera", render: (s) => <strong>{s.camera_name}</strong> },
    { key: "start", header: "Start", render: (s) => formatDateTime(s.started_at) },
    {
      key: "play",
      header: "Playback",
      render: (s) => (
        <Button variant={selected?.id === s.id ? "primary" : "outline"} size="sm" aria-pressed={selected?.id === s.id} onClick={() => setSelected(s)}>
          {selected?.id === s.id ? "Playing" : "Play"}
        </Button>
      ),
    },
    { key: "length", header: "Length", render: (s) => formatDuration(s.duration_seconds) },
    { key: "size", header: "Size", render: (s) => formatBytes(s.size_bytes) },
    {
      key: "expires",
      header: "Deleted",
      render: (s) => (
        <span title={s.expires_at ? formatDateTime(s.expires_at) : undefined}>{formatCountdown(s.expires_at)}</span>
      ),
    },
  ];
  const data = timeline.data;
  return (
    <section>
      <PageHeader
        title="Recordings"
        description="Routine recordings of this campus with AI event markers. Save what matters to the evidence vault before it expires."
        actions={
          <Button variant="secondary" icon={RotateCw} onClick={timeline.reload} loading={timeline.loading && Boolean(data)}>
            Refresh
          </Button>
        }
      />
      <div className="filters" role="search" aria-label="Filter recordings">
        <LocationPicker tree={tree.data ?? []} value={location} onChange={(v) => { setLocation(v); setCamera(""); setSelected(null); }} />
        <select aria-label="Camera" value={camera} onChange={(e) => { setCamera(e.target.value); setSelected(null); }}>
          <option value="">All cameras</option>
          {(data?.cameras ?? []).map((c) => (
            <option key={c.camera.id} value={c.camera.id}>
              {c.camera.display_name}
            </option>
          ))}
        </select>
        <label className="small">
          From <input type="datetime-local" aria-label="From" value={since} onChange={(e) => setSince(e.target.value)} />
        </label>
        <label className="small">
          To <input type="datetime-local" aria-label="To" value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>
        {since || until ? (
          <Button
            variant="ghost"
            onClick={() => {
              setSince("");
              setUntil("");
            }}
          >
            Last 24 hours
          </Button>
        ) : (
          <span className="muted small">Showing the last 24 hours</span>
        )}
      </div>
      {timeline.error ? (
        <ErrorState error={timeline.error} onRetry={timeline.reload} />
      ) : (
        <>
          {data && (
            <TimelineBar
              since={data.since}
              until={data.until}
              segments={data.segments}
              markers={data.markers}
              selected={selected?.id ?? null}
              onSelect={setSelected}
              onMarker={(m) => {
                const at = new Date(m.detected_at).getTime();
                const hit = data.segments.find((s) => s.camera_id === m.camera_id && new Date(s.started_at).getTime() <= at && new Date(s.ended_at).getTime() >= at);
                if (hit) setSelected(hit);
                else toast("error", "No recording covers this alert any more (it may have expired).");
              }}
            />
          )}
          {selected && <SegmentPlayer segment={selected} markers={data?.markers ?? []} onSaved={(title) => toast("success", `"${title}" saved to the evidence vault.`)} />}
          <DataTable caption="Recording segments" columns={columns} rows={data?.segments ?? []} rowKey={(s) => s.id} loading={timeline.loading && !data} empty="No recordings in this period." />
          {data && (
            <p className="muted small" aria-live="polite">
              {data.segments.length} segment{data.segments.length === 1 ? "" : "s"} · {data.markers.length} AI event{data.markers.length === 1 ? "" : "s"} · each kept for {formatSeconds(data.retention_seconds)} after recording
              {data.markers.length > 0 && (
                <>
                  {" "}
                  · <Link to="/alerts">open alerts</Link>
                </>
              )}
            </p>
          )}
        </>
      )}
      {storage.error ? <ErrorState error={storage.error} onRetry={storage.reload} /> : storage.data && <StoragePanel overview={storage.data} onChange={() => setEditingRetention(true)} />}
      {editingRetention && storage.data && (
        <RetentionDialog
          current={storage.data.retention}
          onClose={() => setEditingRetention(false)}
          onSaved={(overview) => {
            setEditingRetention(false);
            toast("success", `Recordings are now kept for ${formatRetention(overview.retention)}.`);
            storage.reload();
          }}
        />
      )}
    </section>
  );
}
