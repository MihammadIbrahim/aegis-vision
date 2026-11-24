import { Activity, Camera, ClipboardList, Download, FileText, RotateCw, ShieldAlert, Siren, Users } from "lucide-react";
import { useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { BarChart, HorizontalBars } from "../../../components/charts/BarChart";
import { Button, DataTable, ErrorState, LoadingState, PageHeader, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { statusLook } from "../../../lib/status";
import { StatGrid, StatTile } from "../../dashboard/components/StatTile";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { formatBytes } from "../../video/labels";
import { analytics, duration, type Preset, type ReportQuery } from "../api";

const PRESETS: [Preset, string][] = [
  ["TODAY", "Today"],
  ["LAST_7_DAYS", "Last 7 days"],
  ["LAST_30_DAYS", "Last 30 days"],
  ["CUSTOM", "Custom range"],
];

/** Reports & Analytics (Org Admin page 22, Supervisor page 18): figures computed from the campus's own
 * records, filtered by period and place, exportable as CSV or PDF. */
export function ReportsPage() {
  const { session } = useAuth();
  const toast = useToast();
  const [preset, setPreset] = useState<Preset>("LAST_7_DAYS");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [place, setPlace] = useState<LocationValue>(NO_LOCATION);
  const tree = useAsync(() => locations.tree(), []);
  const custom = preset === "CUSTOM";
  const query: ReportQuery = {
    period: preset,
    since: custom && from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
    until: custom && to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
    building_id: place.building_id || undefined,
    floor_id: place.floor_id || undefined,
    area_id: place.area_id || undefined,
  };
  const ready = !custom || (from && to);
  const report = useAsync(
    () => (ready ? analytics.report(query) : Promise.reject(new Error("Choose a start and an end date."))),
    [preset, from, to, place.building_id, place.floor_id, place.area_id],
  );
  const fail = (e: unknown) => toast("error", e instanceof Error ? e.message : String(e));

  const r = report.data;
  return (
    <section>
      <PageHeader
        title="Reports & Analytics"
        description={session?.role === "SUPERVISOR" ? "Operational figures for your campus." : "Security operations figures for your campus."}
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={report.reload}>
              Refresh
            </Button>
            <Button variant="secondary" icon={Download} disabled={!ready} onClick={() => analytics.export(query, "csv").catch(fail)}>
              CSV
            </Button>
            <Button variant="secondary" icon={FileText} disabled={!ready} onClick={() => analytics.export(query, "pdf").catch(fail)}>
              PDF
            </Button>
          </>
        }
      />
      <div className="filters" role="search" aria-label="Report filters">
        <select aria-label="Period" value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>
          {PRESETS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        {custom && (
          <>
            <label className="small">
              From <input type="date" aria-label="From date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </label>
            <label className="small">
              To <input type="date" aria-label="To date" value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
          </>
        )}
        <LocationPicker tree={tree.data ?? []} value={place} onChange={setPlace} anyBuildingLabel="All buildings" idPrefix="report" />
      </div>
      {report.error ? (
        <ErrorState error={report.error} onRetry={report.reload} />
      ) : !r ? (
        <LoadingState />
      ) : (
        <>
          <p className="muted small">
            {r.campus} · {formatDateTime(r.period.since)} to {formatDateTime(r.period.until)} (days counted in {r.period.time_zone})
          </p>
          <StatGrid label="Overview">
            <StatTile label="Alerts" icon={Siren} value={r.overview.alerts_total} hint={`${r.overview.alerts_high} high priority`} />
            <StatTile label="Active cases" icon={ShieldAlert} value={r.overview.incidents_active} hint={`${r.overview.incidents_opened} opened, ${r.overview.incidents_resolved} resolved`} />
            <StatTile label="Manual reports" icon={ClipboardList} value={r.overview.manual_reports} />
            <StatTile label="Cameras online" icon={Camera} value={`${r.overview.cameras_online} / ${r.overview.cameras_total}`} hint={`${r.overview.cameras_offline} offline`} emphasis={r.overview.cameras_offline > 0} />
            <StatTile label="Guards deployed" icon={Users} value={`${r.overview.guards_stationed} / ${r.overview.guards_active}`} hint="Stationed / active" />
            <StatTile
              label="Guard acknowledgement"
              icon={Activity}
              value={r.overview.guard_response_rate_percent === null ? "—" : `${r.overview.guard_response_rate_percent}%`}
              hint="Alerts a Guard acknowledged"
            />
          </StatGrid>
          <div className="grid-2">
            <section className="card">
              <BarChart
                title="Alerts per day by priority"
                categories={r.daily.map((d) => d.day.slice(5))}
                series={[
                  { name: "High", values: r.daily.map((d) => d.high) },
                  { name: "Standard", values: r.daily.map((d) => d.standard) },
                ]}
                valueLabel="Day"
              />
            </section>
            <section className="card">
              <BarChart
                title="Cases opened and manual reports per day"
                categories={r.daily.map((d) => d.day.slice(5))}
                series={[
                  { name: "Cases opened", values: r.daily.map((d) => d.incidents) },
                  { name: "Manual reports", values: r.daily.map((d) => d.manual_reports) },
                ]}
                valueLabel="Day"
              />
            </section>
            <section className="card">
              <HorizontalBars title="AI alerts by type" rows={r.alerts_by_type.map((a) => ({ label: statusLook(a.alert_type).label, value: a.count }))} />
              <p className="muted small">People observed by cross-camera Re-ID in the period: {r.person_observations}.</p>
            </section>
            <section className="card">
              <h2 className="chart-title">Response times</h2>
              <dl className="detail-list">
                <div><dt>Alert → first acknowledgement</dt><dd>{duration(r.response_times.acknowledge_avg_seconds)} average · {duration(r.response_times.acknowledge_median_seconds)} median</dd></div>
                <div><dt>Alert → “I'm Responding”</dt><dd>{duration(r.response_times.respond_avg_seconds)} average · {duration(r.response_times.respond_median_seconds)} median</dd></div>
                <div><dt>Alert → resolved</dt><dd>{duration(r.response_times.resolve_avg_seconds)} average ({r.response_times.resolved} resolved)</dd></div>
                <div><dt>Case → resolved</dt><dd>{duration(r.response_times.incident_resolve_avg_seconds)} average ({r.response_times.incidents_resolved} resolved)</dd></div>
              </dl>
            </section>
          </div>
          <section className="card">
            <h2 className="chart-title">Guard activity</h2>
            <DataTable
              caption="Guard activity"
              columns={[
                { key: "guard", header: "Guard", render: (g) => `${g.full_name} · ${g.guard_id}` },
                { key: "received", header: "Alerts received", align: "right", render: (g) => g.received },
                { key: "ack", header: "Acknowledged", align: "right", render: (g) => g.acknowledged },
                { key: "resp", header: "Responding", align: "right", render: (g) => g.responding },
                { key: "ok", header: "Under control", align: "right", render: (g) => g.under_control },
                { key: "more", header: "More Guards", align: "right", render: (g) => g.additional_required },
                { key: "reports", header: "Manual reports", align: "right", render: (g) => g.manual_reports },
              ]}
              rows={r.guard_activity}
              rowKey={(g) => g.guard_id}
              empty="No Guards in this campus."
            />
          </section>
          <div className="grid-2">
            <section className="card">
              <HorizontalBars
                title={`Manual reports by type (${r.manual_reports.reviewed} of ${r.manual_reports.total} reviewed)`}
                rows={Object.entries(
                  r.manual_reports.by_type.reduce<Record<string, number>>((acc, m) => ({ ...acc, [m.report_type]: (acc[m.report_type] ?? 0) + m.count }), {}),
                ).map(([label, value]) => ({ label, value }))}
              />
            </section>
            <section className="card">
              <HorizontalBars
                title="Alerts, cases and reports by place"
                rows={r.locations.map((l) => ({
                  label: [l.building ?? "No location", l.floor, l.area].filter(Boolean).join(" › "),
                  value: l.alerts + l.incidents + l.manual_reports,
                }))}
              />
            </section>
          </div>
          <div className="grid-2">
            <section className="card">
              <h2 className="chart-title">
                Camera uptime {r.camera_uptime.overall_uptime_percent === null ? "" : `· ${r.camera_uptime.overall_uptime_percent}% overall`}
              </h2>
              <DataTable
                caption="Camera uptime"
                columns={[
                  { key: "camera", header: "Camera", render: (c) => c.camera_name },
                  { key: "uptime", header: "Online share", align: "right", render: (c) => (c.uptime_percent === null ? "No data" : `${c.uptime_percent}%`) },
                  { key: "failures", header: "Went offline", align: "right", render: (c) => c.failures },
                  { key: "state", header: "Now", render: (c) => statusLook(c.state).label },
                ]}
                rows={r.camera_uptime.cameras}
                rowKey={(c) => c.camera_id}
                empty="No cameras."
              />
              <p className="muted small">Measured from the recorded online/offline changes; time before a camera's first report is not counted.</p>
            </section>
            <section className="card">
              <BarChart title="Recorded per day" categories={r.storage.daily.map((d) => d.day.slice(5))} series={[{ name: "MB recorded", values: r.storage.daily.map((d) => Math.round(d.bytes / 1048576)) }]} valueLabel="Day" />
              <dl className="detail-list small">
                <div><dt>Recordings kept</dt><dd>{formatBytes(r.storage.recordings_bytes)} ({r.storage.recordings_segments} files)</dd></div>
                <div><dt>Evidence kept</dt><dd>{formatBytes(r.storage.evidence_bytes)} ({r.storage.evidence_items} items)</dd></div>
                <div><dt>Average per day</dt><dd>{formatBytes(r.storage.average_bytes_per_day)}</dd></div>
                <div><dt>Disk full in</dt><dd>{r.storage.projected_days_until_full === null ? "—" : `about ${r.storage.projected_days_until_full} days at this rate`}</dd></div>
                {r.storage.nodes.map((n) => (
                  <div key={n.node}><dt>Disk “{n.node}”</dt><dd>{n.percent_used.toFixed(1)}% used ({formatBytes(n.free_bytes)} free)</dd></div>
                ))}
              </dl>
            </section>
          </div>
        </>
      )}
    </section>
  );
}
