import {
  Bell,
  Camera,
  CameraOff,
  CircleCheck,
  FolderOpen,
  Radio,
  ScanSearch,
  ShieldAlert,
  Siren,
  TriangleAlert,
  UserPlus,
  Users,
  Video,
} from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";

import { BarChart, HorizontalBars } from "../../../components/charts/BarChart";
import { buttonClass, DataTable, StatusBadge, type Column } from "../../../components/ui";
import { useAsync, type AsyncState } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { statusLook } from "../../../lib/status";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import type { Alert } from "../../alerts";
import { analytics, type DashboardFeed } from "../../analytics/api";
import type { MonitoredCamera } from "../../video";
import { LiveTile } from "../../video/components/LiveTile";
import { Panel } from "./Panel";
import { StatGrid, StatTile } from "./StatTile";

/** The dashboard feed is read again every 30 seconds, so counters and feeds stay current. */
export const FEED_REFRESH_MS = 30_000;
const PREVIEW_COUNT = 4;
const PREVIEW_INTERVAL_MS = 5_000;

export function useDashboardFeed(): AsyncState<DashboardFeed> {
  const feed = useAsync(() => analytics.dashboard(), []);
  const { reload } = feed;
  useEffect(() => {
    const timer = window.setInterval(reload, FEED_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [reload]);
  // Live counts (decision D-026): alert, case and notification signals refresh the feed within seconds; the
  // 30-second refresh stays as the fallback.
  useLiveRefresh(
    reload,
    (e) => e.event === "alert.updated" || e.event === "alert.assigned" || e.event === "incident.updated" || e.event === "notification",
    { debounceMs: 1500 },
  );
  return feed;
}

function value(feed: AsyncState<DashboardFeed>, pick: (d: DashboardFeed) => number): string {
  if (feed.data) return String(pick(feed.data));
  return feed.error ? "Unavailable" : "…";
}

/** Items 20 / 19: a banner while a weapon or fight / violence alert is still open. */
export function UrgentBanner({ feed }: { feed: AsyncState<DashboardFeed> }) {
  const urgent = feed.data?.urgent_alerts ?? [];
  if (urgent.length === 0) return null;
  return (
    <section className="notice error dash-banner" role="alert" aria-label="High-priority alerts">
      <ShieldAlert className="icon" aria-hidden="true" />
      <div className="stack gap-0">
        <strong>
          {urgent.length} high-priority alert{urgent.length === 1 ? "" : "s"} still open
        </strong>
        <ul className="plain">
          {urgent.map((a) => (
            <li key={a.id}>
              <Link to={`/alerts/${a.id}`}>
                {statusLook(a.alert_type).label} · {a.camera_name}
              </Link>{" "}
              <span className="small">
                {statusLook(a.status).label} since {formatDateTime(a.created_at)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/** Items 1–11 (Admin) / 1–10 (Supervisor): campus counters; alert figures cover the last 7 days. */
export function CounterTiles({ feed, showSupervisors }: { feed: AsyncState<DashboardFeed>; showSupervisors: boolean }) {
  return (
    <StatGrid label="Campus overview">
      <StatTile label="Total cameras" icon={Camera} to="/cameras" value={value(feed, (d) => d.counters.cameras_total)} />
      <StatTile label="Online cameras" icon={Video} to="/live" value={value(feed, (d) => d.counters.cameras_online)} hint="Streaming now" />
      <StatTile
        label="Offline cameras"
        icon={CameraOff}
        to="/cameras"
        value={value(feed, (d) => d.counters.cameras_offline)}
        emphasis={Boolean(feed.data && feed.data.counters.cameras_offline > 0)}
      />
      <StatTile label="Total Guards" icon={Users} value={value(feed, (d) => d.counters.guards_total)} hint={feed.data ? `${feed.data.counters.guards_active} active` : undefined} />
      <StatTile label="Guards on duty" icon={Radio} value={value(feed, (d) => d.counters.guards_stationed)} hint="Active, with a location" />
      {showSupervisors && (
        <StatTile
          label="Total Supervisors"
          icon={Users}
          value={value(feed, (d) => d.counters.supervisors_total)}
          hint={feed.data ? `${feed.data.counters.supervisors_active} active` : undefined}
        />
      )}
      <StatTile label="Alerts (7 days)" icon={Siren} to="/alerts" value={value(feed, (d) => d.counters.alerts_total)} />
      <StatTile
        label="High priority (7 days)"
        icon={TriangleAlert}
        to="/alerts"
        value={value(feed, (d) => d.counters.alerts_high)}
        hint="Weapon and fight / violence"
      />
      <StatTile
        label="Pending alerts"
        icon={Bell}
        to="/alerts"
        value={value(feed, (d) => d.counters.alerts_pending)}
        hint="New, not yet acknowledged"
        emphasis={Boolean(feed.data && feed.data.counters.alerts_pending > 0)}
      />
      <StatTile label="Active incidents" icon={FolderOpen} to="/incidents" value={value(feed, (d) => d.counters.incidents_active)} hint="Open cases" />
      <StatTile label="Resolved incidents" icon={CircleCheck} to="/incidents" value={value(feed, (d) => d.counters.incidents_resolved)} />
    </StatGrid>
  );
}

type RecentAlert = DashboardFeed["recent_alerts"][number];
type RecentIncident = DashboardFeed["recent_incidents"][number];

const ALERT_FEED_COLUMNS: Column<RecentAlert>[] = [
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
  { key: "where", header: "Camera / location", render: (a) => (a.location ? `${a.camera_name} · ${a.location}` : a.camera_name) },
  { key: "when", header: "Raised", render: (a) => formatDateTime(a.created_at) },
];

/** Item 12 / 11: the newest alerts, refreshed with the feed. */
export function RecentAlertsPanel({ feed }: { feed: AsyncState<DashboardFeed> }) {
  return (
    <Panel id="recent-alerts-title" title="Recent alerts" link={{ to: "/alerts", label: "All alerts" }} state={feed}>
      {(d) => <DataTable caption="Recent alerts" columns={ALERT_FEED_COLUMNS} rows={d.recent_alerts} rowKey={(a) => a.id} empty="No alerts yet." />}
    </Panel>
  );
}

const INCIDENT_COLUMNS: Column<RecentIncident>[] = [
  {
    key: "ref",
    header: "Case",
    render: (i) => (
      <Link to={`/incidents/${i.id}`}>
        <span className="mono">{i.reference}</span> · {i.title}
      </Link>
    ),
  },
  { key: "status", header: "Status", render: (i) => <StatusBadge status={i.status} /> },
  { key: "priority", header: "Priority", render: (i) => <StatusBadge status={i.priority} /> },
  { key: "who", header: "Handled by", render: (i) => i.assigned_name ?? <span className="muted">Not assigned</span> },
  { key: "updated", header: "Updated", render: (i) => formatDateTime(i.updated_at) },
];

/** Item 13 / 12: the most recently updated cases. */
export function RecentIncidentsPanel({ feed }: { feed: AsyncState<DashboardFeed> }) {
  return (
    <Panel id="recent-incidents-title" title="Recent incidents" link={{ to: "/incidents", label: "All incidents" }} state={feed}>
      {(d) => <DataTable caption="Recent incidents" columns={INCIDENT_COLUMNS} rows={d.recent_incidents} rowKey={(i) => i.id} empty="No incident cases yet." />}
    </Panel>
  );
}

/** Item 16 / 15: active Guards and where each is assigned (Standby when no location). */
export function GuardAvailabilityPanel({ feed }: { feed: AsyncState<DashboardFeed> }) {
  return (
    <Panel id="guards-title" title="Guard availability" link={{ to: "/staff", label: "Staff" }} state={feed}>
      {(d) =>
        d.guards.length === 0 ? (
          <p className="muted">No active Guards.</p>
        ) : (
          <>
            <p className="small">
              {d.guards.filter((g) => g.stationed).length} of {d.guards.length} active Guards have a location; the rest are on Standby.
            </p>
            <ul className="plain dash-list">
              {d.guards.map((g) => (
                <li key={g.guard_id}>
                  <span>
                    {g.full_name} <span className="muted small mono">{g.guard_id}</span>
                  </span>
                  <span className="small">{g.stationed ? g.location : <span className="muted">Standby</span>}</span>
                </li>
              ))}
            </ul>
          </>
        )
      }
    </Panel>
  );
}

const ROLE_WORDS: Record<string, string> = { ADMIN: "Admin", SUPERVISOR: "Supervisor", GUARD: "Guard", SYSTEM: "System" };

/** Item 17 / 16: recent audited operations of the campus (Guard responses, status changes, camera changes). */
export function SecurityActivityPanel({ feed }: { feed: AsyncState<DashboardFeed> }) {
  return (
    <Panel id="activity-feed-title" title="Recent security activity" link={{ to: "/audit-logs", label: "Audit logs" }} state={feed}>
      {(d) =>
        d.activity.length === 0 ? (
          <p className="muted">No recorded activity yet.</p>
        ) : (
          <ol className="plain dash-list">
            {d.activity.map((a, n) => (
              <li key={`${a.occurred_at}-${n}`}>
                <span>
                  {a.action.replace(/_/g, " ").toLowerCase()}
                  {a.target_label ? ` · ${a.target_label}` : ""}
                  {a.outcome !== "SUCCESS" && (
                    <>
                      {" "}
                      <StatusBadge status={a.outcome} />
                    </>
                  )}
                </span>
                <span className="muted small">
                  {a.actor_name ?? ROLE_WORDS[a.actor_role] ?? a.actor_role} · {formatDateTime(a.occurred_at)}
                </span>
              </li>
            ))}
          </ol>
        )
      }
    </Panel>
  );
}

function typeTotal(d: DashboardFeed, type: string): number {
  return d.alerts_by_type.filter((a) => a.alert_type === type).reduce((sum, a) => sum + a.count, 0);
}

/** Item 19 / 18: detections by AI module over the last 7 days. */
export function AiDetectionPanel({ feed }: { feed: AsyncState<DashboardFeed> }) {
  return (
    <Panel id="ai-summary-title" title="AI detection summary (7 days)" link={{ to: "/ai-configuration", label: "AI configuration" }} state={feed}>
      {(d) => (
        <dl className="dash-figures">
          <div>
            <dt>Weapons</dt>
            <dd>{typeTotal(d, "WEAPON")}</dd>
          </div>
          <div>
            <dt>Fights / violence</dt>
            <dd>{typeTotal(d, "FIGHT_VIOLENCE")}</dd>
          </div>
          <div>
            <dt>Restricted area</dt>
            <dd>{typeTotal(d, "RESTRICTED_AREA")}</dd>
          </div>
          <div>
            <dt>People tracked (Re-ID observations)</dt>
            <dd>{d.person_observations}</dd>
          </div>
        </dl>
      )}
    </Panel>
  );
}

/** Items 21–22 / 20–21: cases opened per day and alerts by type, last 7 days. */
export function TrendCharts({ feed }: { feed: AsyncState<DashboardFeed> }) {
  return (
    <div className="dash-grid">
      <Panel id="trend-title" title="Incidents trend" link={{ to: "/reports", label: "Reports" }} state={feed}>
        {(d) => (
          <BarChart
            title="Cases opened per day (last 7 days)"
            categories={d.daily.map((r) => r.day.slice(5))}
            series={[{ name: "Cases opened", values: d.daily.map((r) => r.incidents) }]}
            valueLabel="Day"
          />
        )}
      </Panel>
      <Panel id="types-title" title="Alerts by type" link={{ to: "/reports", label: "Reports" }} state={feed}>
        {(d) => {
          const rows = ["WEAPON", "FIGHT_VIOLENCE", "RESTRICTED_AREA"].map((t) => ({ label: statusLook(t).label, value: typeTotal(d, t) }));
          return <HorizontalBars title="Alerts by AI detection (last 7 days)" rows={rows} />;
        }}
      </Panel>
    </div>
  );
}

/** Item 14 / 13: a few live pictures, cameras with open high-priority alerts first. */
export function LivePreviewPanel({ statuses, alerts }: { statuses: AsyncState<MonitoredCamera[]>; alerts: AsyncState<Alert[]> }) {
  return (
    <Panel id="preview-title" title="Live preview" link={{ to: "/live", label: "Live monitoring" }} state={statuses}>
      {(cameras) => {
        const open = (alerts.data ?? []).filter((a) => a.status !== "RESOLVED" && a.status !== "DISMISSED");
        const urgent = new Set(open.filter((a) => a.priority === "HIGH").map((a) => a.camera_id));
        const anyOpen = new Set(open.map((a) => a.camera_id));
        const rank = (c: MonitoredCamera) => (urgent.has(c.camera.id) ? 0 : anyOpen.has(c.camera.id) ? 1 : c.status.state === "ONLINE" ? 2 : 3);
        const picked = [...cameras].sort((a, b) => rank(a) - rank(b)).slice(0, PREVIEW_COUNT);
        return picked.length === 0 ? (
          <p className="muted">No cameras yet.</p>
        ) : (
          <div className="live-grid cols-2 dash-preview">
            {picked.map((c) => (
              <LiveTile key={c.camera.id} item={c} alerts={open.filter((a) => a.camera_id === c.camera.id)} interval={PREVIEW_INTERVAL_MS} />
            ))}
          </div>
        );
      }}
    </Panel>
  );
}

/** Item 23 / 22: shortcuts. A Supervisor's "acknowledge", "escalate" and "dispatch" open the newest
 * pending alert, where taking the alert, escalating and sending chosen Guards happen (D-024). */
export function QuickActions({ role, feed }: { role: "ADMIN" | "SUPERVISOR"; feed: AsyncState<DashboardFeed> }) {
  const pending = feed.data?.recent_alerts.find((a) => a.status === "NEW") ?? feed.data?.recent_alerts.find((a) => a.status !== "RESOLVED" && a.status !== "DISMISSED");
  const alertLink = pending ? `/alerts/${pending.id}` : "/alerts";
  const actions =
    role === "ADMIN"
      ? [
          { to: "/staff", label: "Add Guard", icon: UserPlus },
          { to: "/cameras", label: "Add Camera", icon: Camera },
          { to: "/alerts", label: "View Alerts", icon: Siren },
          { to: "/person-search", label: "Person Search", icon: ScanSearch },
        ]
      : [
          { to: alertLink, label: "Acknowledge Alert", icon: CircleCheck },
          { to: alertLink, label: "Escalate Alert", icon: TriangleAlert },
          { to: alertLink, label: "Dispatch Guard", icon: Radio },
          { to: "/person-search", label: "Person Search", icon: ScanSearch },
        ];
  return (
    <nav className="dash-actions" aria-label="Quick actions">
      {actions.map((a) => (
        <Link key={a.label} to={a.to} className={buttonClass("outline")}>
          <a.icon className="icon" aria-hidden="true" /> {a.label}
        </Link>
      ))}
      {role === "SUPERVISOR" && !pending && feed.data && <span className="muted small">No open alert right now.</span>}
    </nav>
  );
}
