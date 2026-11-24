import { Download, RotateCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { Button, DataTable, ErrorState, PageHeader, Pagination, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { auditLogs, CATEGORY_LABELS, humanAction, type AuditQuery, type AuditRecord } from "../api";

const PAGE = 50;

function Details({ record }: { record: AuditRecord }) {
  const entries = Object.entries(record.details);
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <details>
      <summary className="small">Details ({entries.length})</summary>
      <dl className="detail-list small">
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>{key.replace(/_/g, " ")}</dt>
            <dd className="break-anywhere">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

/** Audit records of the campus (Org Admin page 21; Supervisor page 17: operational records and their own).
 * Read-only: nobody can change or delete a record. Passwords, codes and tokens are never recorded. */
export function AuditLogsPage() {
  const { session } = useAuth();
  const toast = useToast();
  const [category, setCategory] = useState("");
  const [role, setRole] = useState("");
  const [actorText, setActorText] = useState("");
  const [outcome, setOutcome] = useState("");
  const [text, setText] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [debounced, setDebounced] = useState({ q: "", actor: "" });
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced({ q: text.trim(), actor: actorText.trim() });
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [text, actorText]);
  const categories = useAsync(() => auditLogs.categories(), []);
  const query: AuditQuery = useMemo(
    () => ({
      category: category ? [category] : undefined,
      actor_role: role || undefined,
      actor: debounced.actor || undefined,
      outcome: outcome || undefined,
      q: debounced.q || undefined,
      since: from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
      until: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
    }),
    [category, role, debounced, outcome, from, to],
  );
  const page = useAsync(() => auditLogs.page({ ...query, limit: PAGE, offset }), [query, offset]);
  const reset = () => setOffset(0);

  const columns: Column<AuditRecord>[] = [
    { key: "when", header: "Time", render: (r) => formatDateTime(r.occurred_at) },
    {
      key: "actor",
      header: "Acting user",
      render: (r) =>
        r.actor_role === "SYSTEM" ? (
          <span className="muted">System</span>
        ) : (
          <div className="stack gap-0">
            <span>{r.actor_name ?? "Removed account"}</span>
            <span className="muted small mono">
              {r.actor_display_id ?? "—"} · {r.actor_role.toLowerCase()}
            </span>
          </div>
        ),
    },
    {
      key: "action",
      header: "Action",
      render: (r) => (
        <div className="stack gap-0">
          <span>{humanAction(r.action)}</span>
          <span className="muted small">{CATEGORY_LABELS[r.category] ?? r.category}</span>
        </div>
      ),
    },
    {
      key: "target",
      header: "Target",
      render: (r) => (
        <div className="stack gap-0">
          <span>{r.target_label ?? r.target_type.replace(/_/g, " ").toLowerCase()}</span>
          {r.target_id && <span className="muted small mono break-anywhere">{r.target_id}</span>}
        </div>
      ),
    },
    { key: "outcome", header: "Result", render: (r) => <StatusBadge status={r.outcome} /> },
    { key: "details", header: "Details", render: (r) => <Details record={r} /> },
  ];

  return (
    <section>
      <PageHeader
        title="Audit Logs"
        description={
          session?.role === "SUPERVISOR"
            ? "Operational activity of your campus (alerts, Guard responses, cameras, AI, evidence, video, person search, reports, cases) and your own actions."
            : "Every recorded action in your campus. Records cannot be changed or deleted; secrets are never recorded."
        }
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={page.reload}>
              Refresh
            </Button>
            <Button
              variant="secondary"
              icon={Download}
              onClick={() =>
                auditLogs.exportCsv(query).catch((e: unknown) => toast("error", e instanceof Error ? e.message : String(e)))
              }
            >
              Export CSV
            </Button>
          </>
        }
      />
      <div className="filters" role="search" aria-label="Filter audit records">
        <input type="search" aria-label="Search" placeholder="Keyword, action or target ID" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
        <select aria-label="Activity type" value={category} onChange={(e) => { setCategory(e.target.value); reset(); }}>
          <option value="">All activity types</option>
          {(categories.data?.categories ?? []).map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABELS[c] ?? c}
            </option>
          ))}
        </select>
        <select aria-label="Role" value={role} onChange={(e) => { setRole(e.target.value); reset(); }}>
          <option value="">All roles</option>
          <option value="ADMIN">Admins</option>
          <option value="SUPERVISOR">Supervisors</option>
          <option value="GUARD">Guards</option>
          <option value="SYSTEM">System</option>
        </select>
        <input aria-label="User ID" placeholder="User ID (e.g. a Guard ID)" value={actorText} maxLength={64} onChange={(e) => setActorText(e.target.value)} />
        <select aria-label="Result" value={outcome} onChange={(e) => { setOutcome(e.target.value); reset(); }}>
          <option value="">All results</option>
          <option value="SUCCESS">Success</option>
          <option value="FAILURE">Failure</option>
          <option value="DENIED">Denied</option>
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
          <DataTable caption="Audit records" columns={columns} rows={page.data?.items ?? []} rowKey={(r) => r.id} loading={page.loading && !page.data} empty="No records match." />
          {page.data && <Pagination offset={offset} limit={PAGE} total={page.data.total} onChange={setOffset} />}
        </>
      )}
    </section>
  );
}
