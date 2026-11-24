import { Link } from "react-router-dom";

import type { AuditEntry } from "../../api/types";
import { formatDateTime, humanize } from "../../lib/format";
import { DataTable, StatusBadge, type Column } from "../ui";

/** Detail keys never shown, as a second line of defence (the API never sends secrets). */
const SECRET = /(password|token|otp|secret|hash)/i;

export function detailText(details: Record<string, unknown>): string {
  return Object.entries(details)
    .filter(([key, value]) => !SECRET.test(key) && value !== null && value !== undefined && value !== "")
    .filter(([key]) => key !== "organization_code" && key !== "organization_name")
    .map(([key, value]) => `${humanize(key)}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
    .join(" · ");
}

export function AuditTable({
  entries,
  caption,
  loading = false,
  empty = "No audit events.",
  compact = false,
}: {
  entries: AuditEntry[];
  caption: string;
  loading?: boolean;
  empty?: string;
  compact?: boolean;
}) {
  const columns: Column<AuditEntry>[] = [
    { key: "time", header: "Time", render: (e) => <time dateTime={e.occurred_at}>{formatDateTime(e.occurred_at)}</time> },
    {
      key: "actor",
      header: "Actor",
      render: (e) => (
        <span className="stack-tight">
          <StatusBadge status={e.actor_type} />
          {e.actor_username && <span className="small">{e.actor_username}</span>}
        </span>
      ),
    },
    { key: "action", header: "Action", render: (e) => humanize(e.action) },
    {
      key: "org",
      header: "Organization",
      render: (e) =>
        e.organization_id ? (
          <Link to={`/organizations/${encodeURIComponent(e.organization_id)}`}>{e.organization_name ?? e.organization_id}</Link>
        ) : e.organization_name ? (
          e.organization_name
        ) : (
          <span className="muted">—</span>
        ),
    },
    { key: "outcome", header: "Outcome", render: (e) => <StatusBadge status={e.outcome} /> },
    ...(compact
      ? []
      : [
          {
            key: "client",
            header: "IP / client",
            className: "break",
            render: (e: AuditEntry) => (
              <span className="small">
                {e.ip_address ?? "—"}
                {e.user_agent && <span className="muted block">{e.user_agent}</span>}
              </span>
            ),
          },
          { key: "details", header: "Details", className: "break", render: (e: AuditEntry) => <span className="small">{detailText(e.details) || "—"}</span> },
        ]),
  ];
  return <DataTable caption={caption} columns={columns} rows={entries} rowKey={(e) => e.id} loading={loading} empty={empty} />;
}
