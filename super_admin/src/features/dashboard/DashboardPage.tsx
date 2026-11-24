import { Activity, BrainCircuit, Building2, Camera, CirclePause, Ban, CircleCheck, Archive, Users, ScrollText, MapPinned } from "lucide-react";
import { Link } from "react-router-dom";

import { platform } from "../../api/platform";
import { AuditTable } from "../../components/common/AuditTable";
import { Panel } from "../../components/common/Panel";
import { StatGrid, StatTile } from "../../components/common/StatTile";
import { ErrorState, LoadingState, PageHeader, StatusBadge } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { AI_MODULE_LABELS } from "../labels";

/** Platform overview (page 2): counts only, never footage or organization records. */
export function DashboardPage() {
  const overview = useAsync(() => platform.overview(), []);
  if (overview.error) return <ErrorState error={overview.error} onRetry={overview.reload} />;
  if (!overview.data) return <LoadingState />;
  const { counts, health, ai_modules, recent_activity } = overview.data;
  const staff = counts.admins_active + counts.supervisors_active + counts.guards_active;
  return (
    <section>
      <PageHeader
        title="Platform overview"
        description="Aggregated figures across all organizations. No camera feeds, recordings or organization records are shown here."
      />
      <StatGrid label="Organizations">
        <StatTile label="Organizations" icon={Building2} value={counts.organizations_total} to="/organizations" />
        <StatTile label="Active" icon={CircleCheck} value={counts.organizations_active} />
        <StatTile label="Inactive" icon={CirclePause} value={counts.organizations_inactive} />
        <StatTile label="Suspended" icon={Ban} value={counts.organizations_suspended} emphasis={counts.organizations_suspended > 0} />
        <StatTile label="Deleted" icon={Archive} value={counts.deleted_organizations_total} hint="Names only are kept" />
      </StatGrid>
      <StatGrid label="Infrastructure and staff">
        <StatTile label="Campuses" icon={MapPinned} value={counts.campuses_total} hint={`${counts.campuses_active} active`} />
        <StatTile label="Registered cameras" icon={Camera} value={counts.cameras_total} hint="Count only; no online status" />
        <StatTile
          label="Active staff"
          icon={Users}
          value={staff}
          hint={`${counts.admins_active} Admins · ${counts.supervisors_active} Supervisors · ${counts.guards_active} Guards`}
        />
      </StatGrid>
      <div className="dash-grid">
        <Panel id="health-title" title="System health" link={{ to: "/health", label: "Open health" }}>
          {() => (
            <div className="stack">
              <StatusBadge status={health.overall_status} />
              <p>{health.summary}</p>
            </div>
          )}
        </Panel>
        <Panel id="ai-title" title="AI modules" link={{ to: "/ai-settings", label: "AI settings" }}>
          {() => (
            <ul className="plain dash-list">
              {ai_modules.map((m) => (
                <li key={m.key}>
                  <span className="dash-list-main">{AI_MODULE_LABELS[m.key]?.name ?? m.key}</span>
                  <span className="badges">
                    <StatusBadge status={m.enabled ? "ENABLED" : "DISABLED"} />
                    {!m.implemented && <StatusBadge status="PLANNED" />}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
      <Panel id="activity-title" title="Recent platform activity" link={{ to: "/audit-logs", label: "All audit logs" }}>
        {() => <AuditTable entries={recent_activity} caption="Recent platform activity" compact empty="No platform events yet." />}
      </Panel>
      <nav className="quick-links" aria-label="Quick actions">
        <Link className="btn btn-secondary" to="/organizations">
          <Building2 className="icon" aria-hidden="true" /> Organization management
        </Link>
        <Link className="btn btn-secondary" to="/ai-settings">
          <BrainCircuit className="icon" aria-hidden="true" /> Platform AI settings
        </Link>
        <Link className="btn btn-secondary" to="/health">
          <Activity className="icon" aria-hidden="true" /> System health
        </Link>
        <Link className="btn btn-secondary" to="/audit-logs">
          <ScrollText className="icon" aria-hidden="true" /> Audit logs
        </Link>
      </nav>
    </section>
  );
}
