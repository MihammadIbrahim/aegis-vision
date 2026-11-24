import { Image, RotateCw, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { Button, DataTable, ErrorState, PageHeader, Pagination, StatusBadge, type Column } from "../../../components/ui";
import { useAuth } from "../../../auth/AuthContext";
import { useAsync } from "../../../hooks/useAsync";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { formatDateTime } from "../../../lib/format";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { alerts } from "../api";
import { alertLocation, STATUS_OPTIONS, TYPE_OPTIONS } from "../labels";
import type { Alert, AlertQuery } from "../types";

const PAGE = 25;

interface Draft {
  q: string;
  status: string;
  priority: string;
  alert_type: string;
  location: LocationValue;
  from: string;
  to: string;
  mine: boolean;
}

const EMPTY: Draft = { q: "", status: "", priority: "", alert_type: "", location: NO_LOCATION, from: "", to: "", mine: false };

function toQuery(d: Draft, me: string | undefined): AlertQuery {
  return {
    q: d.q.trim() || undefined,
    status: d.status || undefined,
    priority: d.priority || undefined,
    alert_type: d.alert_type || undefined,
    building_id: d.location.building_id || undefined,
    floor_id: d.location.floor_id || undefined,
    area_id: d.location.area_id || undefined,
    since: d.from ? new Date(`${d.from}T00:00:00`).toISOString() : undefined,
    until: d.to ? new Date(`${d.to}T23:59:59`).toISOString() : undefined,
    handled_by: d.mine ? me : undefined,
  };
}

/** The campus's AI alerts. The server lists open alerts first, high priority first, newest first. */
export function AlertsPage() {
  const me = useAuth().session?.account_id;
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [query, setQuery] = useState<AlertQuery>({});
  const [offset, setOffset] = useState(0);
  const tree = useAsync(() => locations.tree(), []);
  // One extra row tells whether there is a next page (the list has no total).
  const list = useAsync(() => alerts.list({ ...query, limit: PAGE + 1, offset }), [query, offset]);
  // New alerts and every status or Guard response change refresh the list live (D-026).
  useLiveRefresh(list.reload, (e) => e.event === "alert.updated" || e.event === "alert.assigned", {
    debounceMs: 800,
    fallbackMs: 30_000,
  });
  const rows = (list.data ?? []).slice(0, PAGE);

  function search(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setQuery(toQuery(draft, me));
  }

  function clear() {
    setDraft(EMPTY);
    setOffset(0);
    setQuery({});
  }

  const columns: Column<Alert>[] = [
    {
      key: "type",
      header: "Alert",
      render: (a) => (
        <Link to={`/alerts/${a.id}`} className="cell-link">
          <StatusBadge status={a.alert_type} />
          <span className="sr-only">: open details</span>
          {a.has_snapshot && <Image className="icon muted" aria-label="Snapshot kept" />}
        </Link>
      ),
    },
    { key: "priority", header: "Priority", render: (a) => <StatusBadge status={a.priority} /> },
    { key: "status", header: "Status", render: (a) => <StatusBadge status={a.status} /> },
    { key: "camera", header: "Camera", render: (a) => a.camera_name },
    { key: "location", header: "Location", render: (a) => alertLocation(a) },
    {
      key: "guards",
      header: "Guards",
      render: (a) => (
        <span className="small" title="Guards the alert went to · acknowledged · responding">
          {a.recipient_count ?? 0} sent · {a.acknowledged_count ?? 0} ack. · {a.responding_count ?? 0} resp.
        </span>
      ),
    },
    { key: "handler", header: "Handled by", render: (a) => a.handled_by_name ?? <span className="muted">—</span> },
    {
      key: "detected",
      header: "Last detected",
      render: (a) => (
        <>
          {formatDateTime(a.last_detected_at)}
          {a.detection_count > 1 && <span className="muted small"> · {a.detection_count} detections</span>}
        </>
      ),
    },
  ];

  return (
    <section>
      <PageHeader
        title="Alerts"
        description="AI-generated alerts for this campus. They are signals for review, not proof. Manual Guard reports are listed separately."
        actions={
          <Button variant="secondary" icon={RotateCw} onClick={list.reload} loading={list.loading && Boolean(list.data)}>
            Refresh
          </Button>
        }
      />
      <form className="filters" onSubmit={search} aria-label="Filter alerts">
        <input type="search" aria-label="Search alerts" placeholder="Alert ID, type, camera or location" value={draft.q} maxLength={200} onChange={(e) => setDraft({ ...draft, q: e.target.value })} />
        <select aria-label="Status" value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
          <option value="">Any status</option>
          {STATUS_OPTIONS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <select aria-label="Priority" value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: e.target.value })}>
          <option value="">Any priority</option>
          <option value="HIGH">High priority</option>
          <option value="STANDARD">Standard</option>
        </select>
        <select aria-label="Alert type" value={draft.alert_type} onChange={(e) => setDraft({ ...draft, alert_type: e.target.value })}>
          <option value="">Any type</option>
          {TYPE_OPTIONS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <LocationPicker tree={tree.data ?? []} value={draft.location} onChange={(location) => setDraft({ ...draft, location })} />
        <label className="small">
          From <input type="date" aria-label="From date" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
        </label>
        <label className="small">
          To <input type="date" aria-label="To date" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
        </label>
        <label className="checkbox small">
          <input type="checkbox" disabled={!me} checked={draft.mine} onChange={(e) => setDraft({ ...draft, mine: e.target.checked })} /> Handled by me
        </label>
        <Button type="submit" icon={Search}>
          Apply
        </Button>
        <Button variant="ghost" onClick={clear}>
          Clear
        </Button>
      </form>
      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : (
        <>
          <DataTable
            caption="Alerts"
            columns={columns}
            rows={rows}
            rowKey={(a) => a.id}
            loading={list.loading && !list.data}
            empty="No alerts match."
          />
          {list.data && (offset > 0 || list.data.length > PAGE) && (
            <Pagination count={rows.length} hasMore={list.data.length > PAGE} limit={PAGE} offset={offset} onChange={setOffset} />
          )}
        </>
      )}
    </section>
  );
}
