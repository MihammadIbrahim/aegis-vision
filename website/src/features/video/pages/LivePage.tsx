import { LayoutGrid, RotateCw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button, EmptyState, ErrorState, LoadingState, PageHeader } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { alerts, type Alert } from "../../alerts";
import { CLOSED_STATUSES } from "../../alerts/types";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { live } from "../api";
import { LiveTile } from "../components/LiveTile";
import { GRID_SIZES, type GridSize } from "../labels";
import type { CameraState, MonitoredCamera } from "../types";

/** How often the camera list and statuses are refreshed. */
export const STATUS_INTERVAL_MS = 15000;

type StateFilter = "" | CameraState;

/** How long typing pauses before the camera search is sent. */
export const SEARCH_DELAY_MS = 300;

/** Live CCTV monitoring: a 1×1 to 4×4 grid of the campus's cameras, search, location and status
 * filters, fullscreen for one camera, badges for each camera's open alerts, and (optionally) the AI
 * detections of each camera's vision worker drawn on the picture. Opening the page
 * (and a fullscreen view) is audited by the server. */
export function LivePage() {
  const tree = useAsync(() => locations.tree(), []);
  const [location, setLocation] = useState<LocationValue>(NO_LOCATION);
  const [state, setState] = useState<StateFilter>("");
  const [size, setSize] = useState<GridSize>(2);
  const [page, setPage] = useState(0);
  const [fullscreen, setFullscreen] = useState<MonitoredCamera | null>(null);
  const [showOverlay, setShowOverlay] = useState(true);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(text), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);
  const query = {
    q: q.trim() || undefined,
    building_id: location.building_id || undefined,
    floor_id: location.floor_id || undefined,
    area_id: location.area_id || undefined,
  };
  const list = useAsync(() => live.cameras(query), [query.q, query.building_id, query.floor_id, query.area_id]);
  // Open alerts drive the AI badges; a failure here only hides the badges.
  const open = useAsync(() => alerts.list({ limit: 200, offset: 0 }), []);
  const alertsByCamera = useMemo(() => {
    const map = new Map<string, Alert[]>();
    for (const a of open.data ?? []) {
      if (CLOSED_STATUSES.includes(a.status)) continue;
      map.set(a.camera_id, [...(map.get(a.camera_id) ?? []), a]);
    }
    return map;
  }, [open.data]);

  useEffect(() => {
    live.startSession([]).catch(() => undefined); // the audit record; failures do not block viewing
  }, []);
  useEffect(() => {
    const timer = window.setInterval(() => {
      list.reload();
      open.reload();
    }, STATUS_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [list.reload, open.reload]);

  const cameras = useMemo(() => (list.data ?? []).filter((c) => !state || c.status.state === state), [list.data, state]);
  const perPage = size * size;
  const pages = Math.max(1, Math.ceil(cameras.length / perPage));
  const shown = cameras.slice(Math.min(page, pages - 1) * perPage, Math.min(page, pages - 1) * perPage + perPage);
  const online = (list.data ?? []).filter((c) => c.status.state === "ONLINE").length;
  const offline = (list.data ?? []).filter((c) => c.status.state === "OFFLINE").length;

  async function openFullscreen(item: MonitoredCamera) {
    setFullscreen(item);
    live.startSession([item.camera.id]).catch(() => undefined);
  }

  return (
    <section>
      <PageHeader
        title="Live Cameras"
        description={
          list.data
            ? `${online} of ${list.data.length} camera${list.data.length === 1 ? "" : "s"} online · ${offline} offline · frames refresh every second`
            : "Live view of this campus's cameras."
        }
        actions={
          <Button
            variant="secondary"
            icon={RotateCw}
            onClick={() => {
              list.reload();
              open.reload();
            }}
            loading={list.loading && Boolean(list.data)}
          >
            Refresh
          </Button>
        }
      />
      <div className="filters" role="search" aria-label="Filter cameras">
        <input type="search" aria-label="Search" placeholder="Camera name or Camera ID" value={text} maxLength={200} onChange={(e) => { setText(e.target.value); setPage(0); }} />
        <LocationPicker tree={tree.data ?? []} value={location} onChange={(v) => { setLocation(v); setPage(0); }} />
        <select aria-label="Status" value={state} onChange={(e) => { setState(e.target.value as StateFilter); setPage(0); }}>
          <option value="">All statuses</option>
          <option value="ONLINE">Online</option>
          <option value="OFFLINE">Offline</option>
          <option value="UNKNOWN">Unknown</option>
        </select>
        <label className="checkbox small">
          <input type="checkbox" checked={showOverlay} onChange={(e) => setShowOverlay(e.target.checked)} /> AI detection overlay
        </label>
        <div className="actions" role="group" aria-label="Grid size">
          <LayoutGrid className="icon muted" aria-hidden="true" />
          {GRID_SIZES.map((n) => (
            <Button key={n} size="sm" variant={size === n ? "primary" : "outline"} aria-pressed={size === n} onClick={() => { setSize(n); setPage(0); }}>
              {n}×{n}
            </Button>
          ))}
        </div>
      </div>
      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingState />
      ) : cameras.length === 0 ? (
        <EmptyState title="No cameras to show" message={state || location.building_id || q ? "No camera matches the filters." : "Add cameras on the Cameras page."} />
      ) : (
        <>
          <div className={`live-grid cols-${size}`} role="list" aria-label="Live cameras">
            {shown.map((item) => (
              <div role="listitem" key={item.camera.id}>
                <LiveTile item={item} alerts={alertsByCamera.get(item.camera.id)} onFullscreen={openFullscreen} showOverlay={showOverlay} />
              </div>
            ))}
          </div>
          {pages > 1 && (
            <nav className="actions" aria-label="Camera pages">
              <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <span className="muted small">
                Page {Math.min(page, pages - 1) + 1} of {pages}
              </span>
              <Button variant="outline" size="sm" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>
                Next
              </Button>
            </nav>
          )}
        </>
      )}
      {fullscreen && (
        <div className="live-fullscreen" role="dialog" aria-modal="true" aria-label={`${fullscreen.camera.display_name} fullscreen`}>
          <div className="live-fullscreen-bar">
            <strong>{fullscreen.camera.display_name}</strong>
            <Button variant="ghost" icon={X} onClick={() => setFullscreen(null)} autoFocus>
              Close
            </Button>
          </div>
          <LiveTile item={fullscreen} alerts={alertsByCamera.get(fullscreen.camera.id)} interval={500} showOverlay={showOverlay} />
        </div>
      )}
    </section>
  );
}
