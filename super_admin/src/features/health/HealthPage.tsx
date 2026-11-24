import { HardDrive, RotateCw } from "lucide-react";

import { platform } from "../../api/platform";
import type { ServiceHealth } from "../../api/types";
import { Button, ErrorState, LoadingState, PageHeader, StatusBadge } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { formatBytes, formatDateTime, formatDuration } from "../../lib/format";

function Metrics({ service }: { service: ServiceHealth }) {
  const m = service.metrics;
  const rows: Array<[string, string]> = [];
  if (typeof m.uptime_seconds === "number") rows.push(["Uptime", formatDuration(m.uptime_seconds)]);
  if (typeof m.server_version === "string") rows.push(["Version", m.server_version]);
  if (typeof m.database_size_bytes === "number") rows.push(["Database size", formatBytes(m.database_size_bytes)]);
  if (typeof m.pgvector_installed === "boolean") rows.push(["pgvector", m.pgvector_installed ? "Installed" : "Not installed"]);
  if (Array.isArray(m.extensions)) rows.push(["Extensions", m.extensions.join(", ") || "—"]);
  const pool = m.connection_pool as Record<string, unknown> | undefined;
  if (pool && typeof pool.checkedout === "number") rows.push(["Pool connections in use", String(pool.checkedout)]);
  if (typeof m.used_percent === "number") rows.push(["Used", `${m.used_percent}%`]);
  if (typeof m.detection_intake_enabled === "boolean") rows.push(["Detection intake", m.detection_intake_enabled ? "Enabled" : "Off"]);
  if (rows.length === 0) return null;
  return (
    <dl className="metrics">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Platform System Health (page 6). Every load measures again; services that are not deployed are
 * shown as such and never as running. */
export function HealthPage() {
  const health = useAsync(() => platform.health(), []);
  const data = health.data;
  return (
    <section>
      <PageHeader
        title="System health"
        description="Measured now: the Core API, the database and the media storage. Planned services that are not deployed are listed honestly."
        actions={
          <Button variant="secondary" icon={RotateCw} onClick={health.reload} loading={health.loading && Boolean(data)}>
            Refresh
          </Button>
        }
      />
      {health.error ? (
        <ErrorState error={health.error} onRetry={health.reload} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <>
          <section className="card health-summary" aria-label="Overall status">
            <StatusBadge status={data.overall_status} />
            <span>{data.summary}</span>
            <span className="muted small">Checked {formatDateTime(data.checked_at)}</span>
          </section>
          {data.problems.length > 0 && (
            <ul className="notice warning plain" aria-label="Problems">
              {data.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          <div className="service-grid">
            {data.services.map((s) => (
              <section key={s.key} className={`card service-card${s.status === "NOT_DEPLOYED" ? " is-muted" : ""}`} aria-labelledby={`svc-${s.key}`}>
                <div className="card-head">
                  <h2 id={`svc-${s.key}`}>{s.name}</h2>
                  <StatusBadge status={s.status} />
                </div>
                <p className="small">{s.detail}</p>
                {s.latency_ms !== null && <p className="small">Response time: {s.latency_ms} ms</p>}
                <Metrics service={s} />
              </section>
            ))}
          </div>
          <section className="card" aria-labelledby="storage-title">
            <div className="card-head">
              <h2 id="storage-title">
                <HardDrive className="icon inline-icon" aria-hidden="true" /> Storage capacity
              </h2>
              <StatusBadge status={data.storage.level} />
            </div>
            {data.storage.used_percent !== null && (
              <div
                className={`meter level-${data.storage.level.toLowerCase()}`}
                role="meter"
                aria-label="Storage used"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={data.storage.used_percent}
              >
                <span style={{ width: `${Math.min(100, data.storage.used_percent)}%` }} />
              </div>
            )}
            <dl className="metrics">
              <div>
                <dt>Folder</dt>
                <dd className="mono break-anywhere">{data.storage.path}</dd>
              </div>
              <div>
                <dt>Reachable / writable</dt>
                <dd>
                  {data.storage.accessible ? "Yes" : "No"} / {data.storage.writable ? "Yes" : "No"}
                </dd>
              </div>
              <div>
                <dt>Used</dt>
                <dd>
                  {formatBytes(data.storage.used_bytes)} of {formatBytes(data.storage.total_bytes)}
                  {data.storage.used_percent !== null ? ` (${data.storage.used_percent}%)` : ""}
                </dd>
              </div>
              <div>
                <dt>Free</dt>
                <dd>{formatBytes(data.storage.free_bytes)}</dd>
              </div>
              <div>
                <dt>Warning / critical at</dt>
                <dd>
                  {data.storage.warning_percent}% / {data.storage.critical_percent}%
                </dd>
              </div>
            </dl>
          </section>
          <p className="muted small">Uptime percentages and an error history are not available: the platform keeps no health history yet.</p>
        </>
      )}
    </section>
  );
}
