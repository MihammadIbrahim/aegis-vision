import { Bell, Building2, Camera, ClipboardList, DoorClosed, Siren, TriangleAlert, Users } from "lucide-react";
import { Link } from "react-router-dom";

import { DataTable, StatusBadge, type Column } from "../../../components/ui";
import type { AsyncState } from "../../../hooks/useAsync";
import { formatDateTime, formatLocationPath } from "../../../lib/format";
import type { Alert } from "../../alerts";
import type { BuildingNode } from "../../locations";
import type { ManualReport } from "../../manual-reports";
import { notificationTarget, type AppNotification } from "../../notifications";
import type { RestrictedArea } from "../../restricted-access";
import type { MonitoredCamera } from "../../video";
import { LIST_CAP } from "../api";
import { formatCount, summarizeAlerts, summarizeLocations, summarizeReports, type Count } from "../metrics";
import type { DashboardOrganization } from "../types";
import { BarList } from "./BarList";
import { Panel } from "./Panel";
import { StatGrid, StatTile } from "./StatTile";

export const NOTIFICATION_LIMIT = 100;

/** A tile value from one data source: the figure, or a short word while loading or on error. */
function shown<T>(state: AsyncState<T>, value: (data: T) => string): string {
  if (state.error) return "Unavailable";
  if (state.data === undefined) return "…";
  return value(state.data);
}

function count(c: Count): string {
  return formatCount(c);
}

// --- Organization and campus ------------------------------------------------------------------------

export function OrganizationPanel({
  organization,
  campus,
  role,
}: {
  organization: DashboardOrganization;
  campus: { name: string; status: string };
  role: string;
}) {
  return (
    <section className="card dash-org" aria-label="Organization and campus">
      <dl className="dash-org-list">
        <div>
          <dt>Organization</dt>
          <dd>{organization.name}</dd>
        </div>
        <div>
          <dt>Organization ID</dt>
          <dd className="mono">{organization.organization_id}</dd>
        </div>
        <div>
          <dt>Campus</dt>
          <dd>{campus.name}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>
            <StatusBadge status={campus.status} />
          </dd>
        </div>
        <div>
          <dt>Your role</dt>
          <dd>{role}</dd>
        </div>
      </dl>
    </section>
  );
}

// --- Security operations ----------------------------------------------------------------------------

export function OperationsTiles({
  alerts,
  reports,
  notifications,
}: {
  alerts: AsyncState<Alert[]>;
  reports: AsyncState<ManualReport[]>;
  notifications: AsyncState<AppNotification[]>;
}) {
  const summary = alerts.data ? summarizeAlerts(alerts.data, LIST_CAP) : null;
  const unread = notifications.data
    ? { value: notifications.data.filter((n) => !n.read_at).length, atLeast: notifications.data.length >= NOTIFICATION_LIMIT }
    : null;
  return (
    <StatGrid label="Security operations">
      <StatTile label="Open alerts" icon={Siren} to="/alerts" value={shown(alerts, () => count(summary!.open))} hint="Not resolved or dismissed" />
      <StatTile
        label="High-priority open"
        icon={TriangleAlert}
        to="/alerts"
        emphasis={Boolean(summary && summary.highPriorityOpen.value > 0)}
        value={shown(alerts, () => count(summary!.highPriorityOpen))}
        hint="Weapon and fight / violence"
      />
      <StatTile label="Not yet acknowledged" icon={Siren} to="/alerts" value={shown(alerts, () => count(summary!.unacknowledged))} hint="Open alerts still New" />
      <StatTile
        label="Reports awaiting review"
        icon={ClipboardList}
        to="/manual-reports"
        value={shown(reports, (rows) => count(summarizeReports(rows, LIST_CAP).awaiting))}
        hint="Manual reports from Guards"
      />
      <StatTile label="Unread notifications" icon={Bell} to="/notifications" value={shown(notifications, () => count(unread!))} hint="Yours only" />
    </StatGrid>
  );
}

const ATTENTION_COLUMNS: Column<Alert>[] = [
  {
    key: "type",
    header: "Alert",
    render: (a) => (
      <Link to={`/alerts/${a.id}`} className="cell-link">
        <StatusBadge status={a.alert_type} />
        <span className="sr-only">: open details</span>
      </Link>
    ),
  },
  { key: "priority", header: "Priority", render: (a) => <StatusBadge status={a.priority} /> },
  { key: "status", header: "Status", render: (a) => <StatusBadge status={a.status} /> },
  { key: "camera", header: "Camera", render: (a) => a.camera_name },
  {
    key: "location",
    header: "Location",
    render: (a) => formatLocationPath(a.location.building_name, a.location.floor_name, a.location.area_name),
  },
  { key: "last", header: "Last detected", render: (a) => formatDateTime(a.last_detected_at) },
];

export function AttentionPanel({ alerts }: { alerts: AsyncState<Alert[]> }) {
  return (
    <Panel id="attention-title" title="Open alerts needing attention" link={{ to: "/alerts", label: "All alerts" }} state={alerts}>
      {(rows) => {
        const s = summarizeAlerts(rows, LIST_CAP);
        return (
          <>
            <DataTable
              caption="Open alerts needing attention"
              columns={ATTENTION_COLUMNS}
              rows={s.attention}
              rowKey={(a) => a.id}
              empty="No open alerts."
            />
            <p className="muted small">
              High priority first, then newest. AI detections are signals for review, not proof.
            </p>
          </>
        );
      }}
    </Panel>
  );
}

export function AlertBreakdownPanel({ alerts }: { alerts: AsyncState<Alert[]> }) {
  return (
    <Panel id="breakdown-title" title="Open alerts by status and type" state={alerts}>
      {(rows) => {
        const s = summarizeAlerts(rows, LIST_CAP);
        return (
          <div className="stack">
            <h4 className="dash-subhead">By status</h4>
            <BarList
              label="Open alerts by status"
              empty="No open alerts."
              bars={s.openByStatus.map((b) => ({ key: b.key, name: b.key, value: b.value, label: <StatusBadge status={b.key} /> }))}
            />
            {s.open.value > 0 && <h4 className="dash-subhead">By type</h4>}
            {s.open.value > 0 && (
              <BarList
                label="Open alerts by type"
                empty="No open alerts."
                bars={s.openByType.map((b) => ({ key: b.key, name: b.key, value: b.value, label: <StatusBadge status={b.key} /> }))}
              />
            )}
            {s.openIncomplete && (
              <p className="notice warning small">
                More than {LIST_CAP} alerts are open; these figures count the first {LIST_CAP} only.
              </p>
            )}
          </div>
        );
      }}
    </Panel>
  );
}

export function RecentReportsPanel({ reports }: { reports: AsyncState<ManualReport[]> }) {
  return (
    <Panel id="reports-title" title="Latest manual reports" link={{ to: "/manual-reports", label: "All reports" }} state={reports}>
      {(rows) => {
        const recent = summarizeReports(rows, LIST_CAP).recent;
        return recent.length === 0 ? (
          <p className="muted">No manual reports yet.</p>
        ) : (
          <ul className="plain dash-list">
            {recent.map((r) => (
              <li key={r.id}>
                <span className="dash-list-main">
                  <Link to={`/manual-reports/${r.id}`}>{r.report_type}</Link>{" "}
                  <StatusBadge status={r.status} />
                </span>
                <span className="muted small">
                  {r.guard_name} · {formatLocationPath(r.building_name, r.floor_name, r.area_name)} ·{" "}
                  <time dateTime={r.created_at}>{formatDateTime(r.created_at)}</time>
                </span>
              </li>
            ))}
          </ul>
        );
      }}
    </Panel>
  );
}

export function ActivityPanel({ notifications }: { notifications: AsyncState<AppNotification[]> }) {
  return (
    <Panel id="activity-title" title="Recent activity" link={{ to: "/notifications", label: "All notifications" }} state={notifications}>
      {(rows) =>
        rows.length === 0 ? (
          <p className="muted">No notifications yet.</p>
        ) : (
          <ul className="plain dash-list">
            {rows.slice(0, 5).map((n) => {
              const to = notificationTarget(n)?.to ?? null;
              return (
                <li key={n.id}>
                  <span className="dash-list-main">
                    {!n.read_at && <StatusBadge status="UNREAD" />} {to ? <Link to={to}>{n.title}</Link> : n.title}
                  </span>
                  <time className="muted small" dateTime={n.created_at}>
                    {formatDateTime(n.created_at)}
                  </time>
                </li>
              );
            })}
          </ul>
        )
      }
    </Panel>
  );
}

// --- Site: locations, cameras, Guards ---------------------------------------------------------------

const BUILDING_COLUMNS: Column<BuildingNode>[] = [
  { key: "name", header: "Building / perimeter", render: (b) => b.name },
  { key: "kind", header: "Type", render: (b) => (b.kind === "OUTDOOR_PERIMETER" ? "Outdoor perimeter" : "Indoor") },
  { key: "floors", header: "Floors", align: "right", render: (b) => b.floors.length },
  { key: "areas", header: "Areas", align: "right", render: (b) => b.areas.length + b.floors.reduce((n, f) => n + f.areas.length, 0) },
  { key: "cameras", header: "Cameras", align: "right", render: (b) => b.camera_count ?? "—" },
  { key: "guards", header: "Guards assigned", align: "right", render: (b) => b.guard_count ?? "—" },
];

/** Locations, cameras and Guard assignments of the campus. ``cameras`` and ``activeGuards`` come
 * from the dashboard endpoint; linked/assigned figures from the location tree. */
export function SitePanel({
  tree,
  cameras,
  activeGuards,
  statuses,
}: {
  tree: AsyncState<BuildingNode[]>;
  cameras: number;
  activeGuards: number;
  /** Live online/offline statuses (GET /live/cameras); omitted in tests that do not mock it. */
  statuses?: AsyncState<MonitoredCamera[]>;
}) {
  const online = statuses?.data?.filter((m) => m.status.state === "ONLINE").length;
  const offline = statuses?.data?.filter((m) => m.status.state === "OFFLINE").length;
  return (
    <Panel id="site-title" title="Locations, cameras and Guards" state={tree}>
      {(buildings) => {
        const s = summarizeLocations(buildings);
        const unlinked = Math.max(0, cameras - s.camerasLinked);
        return (
          <div className="stack">
            <StatGrid label="Site">
              <StatTile label="Buildings" icon={Building2} value={s.buildings} hint={`${s.perimeters} outdoor perimeter${s.perimeters === 1 ? "" : "s"}`} />
              <StatTile label="Floors / areas" value={`${s.floors} / ${s.areas}`} />
              <StatTile
                label="Cameras"
                icon={Camera}
                value={cameras}
                hint={unlinked > 0 ? `${unlinked} without a location` : "All linked to a location"}
                emphasis={unlinked > 0}
              />
              {statuses && (
                <StatTile
                  label="Cameras online"
                  icon={Camera}
                  to="/live"
                  value={shown(statuses, (list) => `${list.filter((m) => m.status.state === "ONLINE").length} / ${list.length}`)}
                  hint={offline ? `${offline} offline` : online === undefined ? "Live Cameras" : "None offline"}
                  emphasis={Boolean(offline)}
                />
              )}
              <StatTile label="Active Guards" icon={Users} value={activeGuards} hint={`${s.guardsAssigned} with an assigned location`} />
            </StatGrid>
            <p className="muted small">
              Alerts reach Guards assigned at the camera's location. Cameras without a location raise alerts for
              staff only, and Guards without a location receive alerts only through escalation. Online/offline status comes
              from the video ingestion service; cameras it has not reported are "Unknown".
            </p>
            <DataTable caption="Buildings and perimeters" columns={BUILDING_COLUMNS} rows={buildings} rowKey={(b) => b.id} empty="No buildings or perimeters yet." />
          </div>
        );
      }}
    </Panel>
  );
}

// --- Restricted areas ---------------------------------------------------------------------------------

export function RestrictedAreasPanel({ areas, manage }: { areas: AsyncState<RestrictedArea[]>; manage: boolean }) {
  return (
    <Panel
      id="restricted-title"
      title="Restricted areas"
      link={{ to: "/restricted-access", label: manage ? "Manage access" : "Who is authorized" }}
      state={areas}
    >
      {(rows) =>
        rows.length === 0 ? (
          <p className="muted">No area is marked as restricted.</p>
        ) : (
          <ul className="plain dash-list">
            {rows.map((a) => (
              <li key={a.area_id}>
                <span className="dash-list-main">
                  <DoorClosed className="icon" aria-hidden="true" /> {a.area_name}
                </span>
                <span className="muted small">
                  {formatLocationPath(a.building_name, a.floor_name)} · {a.active_grants} current access grant
                  {a.active_grants === 1 ? "" : "s"}
                </span>
              </li>
            ))}
          </ul>
        )
      }
    </Panel>
  );
}
