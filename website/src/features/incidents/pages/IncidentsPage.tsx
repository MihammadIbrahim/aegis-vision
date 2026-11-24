import { Plus, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Button, DataTable, ErrorState, PageHeader, Pagination, StatusBadge, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { formatDateTime } from "../../../lib/format";
import { statusLook } from "../../../lib/status";
import { incidents, STATUS_ORDER, type Incident, type IncidentPriority, type IncidentStatus } from "../api";
import { IncidentForm } from "../components/IncidentForm";

const PAGE = 25;

/** Incident cases of the campus (Org Admin page 6, Supervisor page 6). */
export function IncidentsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<IncidentStatus | "ACTIVE" | "">("ACTIVE");
  const [priority, setPriority] = useState<IncidentPriority | "">("");
  const [assigned, setAssigned] = useState("");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(text.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [text]);
  const statuses: IncidentStatus[] | undefined =
    status === "ACTIVE" ? ["OPEN", "UNDER_INVESTIGATION", "RESPONDING"] : status ? [status] : undefined;
  const page = useAsync(
    () =>
      incidents.list({
        status: statuses,
        priority,
        assigned: assigned || undefined,
        q: q || undefined,
        since: from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
        until: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
        limit: PAGE,
        offset,
      }),
    [status, priority, assigned, q, from, to, offset],
  );
  useLiveRefresh(page.reload, (e) => e.event === "incident.updated", { debounceMs: 800, fallbackMs: 30_000 });
  const counts = page.data?.counts;
  const reset = () => setOffset(0);

  const columns: Column<Incident>[] = [
    {
      key: "case",
      header: "Case",
      render: (i) => (
        <div className="stack gap-0">
          <Link to={`/incidents/${i.id}`}>
            <strong>{i.reference}</strong> · {i.title}
          </Link>
          <span className="muted small">{i.location ?? i.camera_name ?? "No location"}</span>
        </div>
      ),
    },
    { key: "status", header: "Status", render: (i) => <StatusBadge status={i.status} /> },
    { key: "priority", header: "Priority", render: (i) => <StatusBadge status={i.priority} /> },
    { key: "assigned", header: "Assigned to", render: (i) => i.assigned_name ?? <span className="muted">Not assigned</span> },
    { key: "links", header: "Linked", render: (i) => `${i.alert_count} alerts · ${i.report_count} reports · ${i.evidence_count} evidence` },
    { key: "when", header: "Happened", render: (i) => formatDateTime(i.occurred_at) },
    { key: "updated", header: "Last update", render: (i) => formatDateTime(i.updated_at) },
  ];

  return (
    <section>
      <PageHeader
        title="Incidents & Investigations"
        description="Cases that bring together AI alerts, Guards' manual reports, saved evidence, notes and the case chat."
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={page.reload}>
              Refresh
            </Button>
            <Button icon={Plus} onClick={() => setCreating(true)}>
              Open a case
            </Button>
          </>
        }
      />
      <div className="tabs" role="tablist" aria-label="Case status">
        {(["ACTIVE", "", ...STATUS_ORDER] as const).map((s) => {
          const label = s === "ACTIVE" ? "Active" : s === "" ? "All" : statusLook(s).label;
          const count =
            counts && (s === "ACTIVE" ? counts.OPEN + counts.UNDER_INVESTIGATION + counts.RESPONDING : s === "" ? Object.values(counts).reduce((a, b) => a + b, 0) : counts[s]);
          return (
            <button key={s || "ALL"} type="button" role="tab" aria-selected={status === s} className={`tab${status === s ? " is-active" : ""}`} onClick={() => { setStatus(s); reset(); }}>
              {label}
              {counts !== undefined ? ` (${count})` : ""}
            </button>
          );
        })}
      </div>
      <div className="filters" role="search" aria-label="Find cases">
        <input type="search" aria-label="Search" placeholder="Case number, title, place or person" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
        <select aria-label="Priority" value={priority} onChange={(e) => { setPriority(e.target.value as IncidentPriority | ""); reset(); }}>
          <option value="">All priorities</option>
          <option value="HIGH">High</option>
          <option value="STANDARD">Standard</option>
        </select>
        <select aria-label="Assigned" value={assigned} onChange={(e) => { setAssigned(e.target.value); reset(); }}>
          <option value="">Anyone</option>
          <option value="me">Assigned to me</option>
          <option value="none">Not assigned</option>
        </select>
        <label className="small">
          From <input type="date" aria-label="From date" value={from} onChange={(e) => { setFrom(e.target.value); reset(); }} />
        </label>
        <label className="small">
          To <input type="date" aria-label="To date" value={to} onChange={(e) => { setTo(e.target.value); reset(); }} />
        </label>
      </div>
      {page.error ? (
        <ErrorState error={page.error} onRetry={page.reload} />
      ) : (
        <>
          <DataTable caption="Incident cases" columns={columns} rows={page.data?.items ?? []} rowKey={(i) => i.id} loading={page.loading && !page.data} empty="No cases match." />
          {page.data && <Pagination offset={offset} limit={PAGE} total={page.data.total} onChange={setOffset} />}
        </>
      )}
      {creating && (
        <IncidentForm
          onClose={() => setCreating(false)}
          onSaved={(created) => {
            setCreating(false);
            navigate(`/incidents/${created.id}`);
          }}
        />
      )}
    </section>
  );
}
