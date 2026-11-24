import { CircleAlert, Contact, Users, UserCog } from "lucide-react";

import { useAuth } from "../../../auth/AuthContext";
import { ErrorState, LoadingState, PageHeader } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { alerts } from "../../alerts";
import { locations } from "../../locations";
import { manualReports } from "../../manual-reports";
import { notifications } from "../../notifications";
import { personnel } from "../../personnel";
import { access } from "../../restricted-access";
import { live } from "../../video";
import { dashboard, LIST_CAP } from "../api";
import {
  ActivityPanel,
  AlertBreakdownPanel,
  AttentionPanel,
  NOTIFICATION_LIMIT,
  OperationsTiles,
  OrganizationPanel,
  RecentReportsPanel,
  RestrictedAreasPanel,
  SitePanel,
} from "../components/Sections";
import {
  AiDetectionPanel,
  CounterTiles,
  GuardAvailabilityPanel,
  LivePreviewPanel,
  QuickActions,
  RecentAlertsPanel,
  RecentIncidentsPanel,
  SecurityActivityPanel,
  TrendCharts,
  UrgentBanner,
  useDashboardFeed,
} from "../components/Overview";
import { StatGrid, StatTile } from "../components/StatTile";

/** The sources both dashboards share. Every request is scoped to the caller's own campus by the
 * server; none takes an organization or campus ID. */
function useOperations() {
  const ops = {
    alerts: useAsync(() => alerts.list({ limit: LIST_CAP, offset: 0 }), []),
    reports: useAsync(() => manualReports.list(LIST_CAP, 0), []),
    notifications: useAsync(() => notifications.list(false, NOTIFICATION_LIMIT), []),
    tree: useAsync(() => locations.tree(), []),
    areas: useAsync(() => access.areas(false), []),
    statuses: useAsync(() => live.cameras(), []),
  };
  // Live figures (D-026): alert signals refresh the alert sources, notifications the activity list.
  useLiveRefresh(ops.alerts.reload, (e) => e.event === "alert.updated" || e.event === "alert.assigned", { debounceMs: 1500 });
  useLiveRefresh(ops.notifications.reload, (e) => e.event === "notification", { debounceMs: 1500 });
  return ops;
}

/** The campus overview of D-024: counters, feeds, charts, Guard availability, live preview, quick actions. */
function OverviewSections({ role, ops }: { role: "ADMIN" | "SUPERVISOR"; ops: ReturnType<typeof useOperations> }) {
  const feed = useDashboardFeed();
  return (
    <>
      <UrgentBanner feed={feed} />
      <QuickActions role={role} feed={feed} />
      <h2 className="dash-heading">Campus overview</h2>
      <CounterTiles feed={feed} showSupervisors={role === "ADMIN"} />
      <div className="dash-grid">
        <RecentAlertsPanel feed={feed} />
        <RecentIncidentsPanel feed={feed} />
      </div>
      <TrendCharts feed={feed} />
      <div className="dash-grid">
        <AiDetectionPanel feed={feed} />
        <GuardAvailabilityPanel feed={feed} />
      </div>
      <div className="dash-grid">
        <LivePreviewPanel statuses={ops.statuses} alerts={ops.alerts} />
        <SecurityActivityPanel feed={feed} />
      </div>
    </>
  );
}

function OperationsSections({ ops }: { ops: ReturnType<typeof useOperations> }) {
  return (
    <>
      <h2 className="dash-heading">Security operations</h2>
      <OperationsTiles alerts={ops.alerts} reports={ops.reports} notifications={ops.notifications} />
      <AttentionPanel alerts={ops.alerts} />
      <div className="dash-grid">
        <AlertBreakdownPanel alerts={ops.alerts} />
        <RecentReportsPanel reports={ops.reports} />
      </div>
    </>
  );
}

export function AdminDashboard() {
  const summary = useAsync(() => dashboard.admin(), []);
  const ops = useOperations();
  const people = useAsync(() => personnel.directory({ status: "ACTIVE", limit: 1, offset: 0 }), []);
  if (!summary.data) return summary.error ? <ErrorState error={summary.error} onRetry={summary.reload} /> : <LoadingState />;
  const d = summary.data;
  return (
    <section>
      <PageHeader title="Dashboard" description={`${d.campus.name} · ${d.organization.name}`} />
      <OrganizationPanel organization={d.organization} campus={d.campus} role={d.admin.is_main_admin ? "Main Admin" : "Organization Admin"} />
      <OverviewSections role="ADMIN" ops={ops} />
      <OperationsSections ops={ops} />
      <h2 className="dash-heading">Site and staff</h2>
      <StatGrid label="Staff and personnel">
        <StatTile label="Active Admins" icon={UserCog} value={d.summary.active_admins} />
        <StatTile label="Active Supervisors" icon={Users} value={d.summary.active_supervisors} />
        <StatTile
          label="Active personnel"
          icon={Contact}
          to="/personnel"
          value={people.error ? "Unavailable" : people.data ? people.data.total : "…"}
          hint="Personnel Directory"
        />
        {d.summary.pending_campus_requests !== null && (
          <StatTile
            label="Pending campus requests"
            icon={CircleAlert}
            value={d.summary.pending_campus_requests}
            hint="Main Admin approval"
            emphasis={d.summary.pending_campus_requests > 0}
          />
        )}
      </StatGrid>
      <SitePanel tree={ops.tree} cameras={d.summary.cameras} activeGuards={d.summary.active_guards} statuses={ops.statuses} />
      <div className="dash-grid">
        <RestrictedAreasPanel areas={ops.areas} manage />
        <ActivityPanel notifications={ops.notifications} />
      </div>
    </section>
  );
}

export function SupervisorDashboard() {
  const summary = useAsync(() => dashboard.supervisor(), []);
  const ops = useOperations();
  if (!summary.data) return summary.error ? <ErrorState error={summary.error} onRetry={summary.reload} /> : <LoadingState />;
  const d = summary.data;
  return (
    <section>
      <PageHeader title="Dashboard" description={`${d.campus.name} · ${d.organization.name}`} />
      <OrganizationPanel organization={d.organization} campus={d.campus} role="Supervisor (whole campus)" />
      <OverviewSections role="SUPERVISOR" ops={ops} />
      <OperationsSections ops={ops} />
      <h2 className="dash-heading">Site and Guards</h2>
      <SitePanel tree={ops.tree} cameras={d.summary.cameras} activeGuards={d.summary.active_guards} statuses={ops.statuses} />
      <div className="dash-grid">
        <RestrictedAreasPanel areas={ops.areas} manage={false} />
        <ActivityPanel notifications={ops.notifications} />
      </div>
    </section>
  );
}

/** Opens the dashboard of the signed-in role. Each role calls only its own dashboard endpoint. */
export function DashboardPage() {
  const { session } = useAuth();
  if (session?.role === "ADMIN") return <AdminDashboard />;
  if (session?.role === "SUPERVISOR") return <SupervisorDashboard />;
  return <p role="alert" className="notice error">There is no web dashboard for this role.</p>;
}
