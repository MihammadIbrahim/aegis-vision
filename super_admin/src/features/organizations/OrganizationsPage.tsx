import { Archive, Download, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { platform } from "../../api/platform";
import type { Organization, OrganizationQuery, OrganizationSort, OrganizationStatus, SortOrder } from "../../api/types";
import { Button, DataTable, ErrorState, PageHeader, Pagination, StatusBadge, useToast, type Column } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { formatDateTime } from "../../lib/format";

export const PAGE_SIZE = 25;
export const SEARCH_DELAY_MS = 300;

const SORTS: Array<[OrganizationSort, string]> = [
  ["created_at", "Registration date"],
  ["name", "Name"],
  ["camera_count", "Camera count"],
];

const COLUMNS: Column<Organization>[] = [
  {
    key: "name",
    header: "Organization",
    render: (o) => (
      <span className="stack-tight">
        <Link to={`/organizations/${encodeURIComponent(o.organization_id)}`}>
          <strong>{o.name}</strong>
        </Link>
        <span className="mono small">{o.organization_id}</span>
      </span>
    ),
  },
  {
    key: "contact",
    header: "Official contact",
    className: "break",
    render: (o) => (
      <span className="small">
        {o.official_email ?? "—"}
        <span className="muted block">{o.official_phone_number ?? ""}</span>
      </span>
    ),
  },
  {
    key: "admin",
    header: "Main Admin",
    render: (o) =>
      o.main_admin ? (
        <span className="small">
          {o.main_admin.full_name}
          <span className="muted block">{o.main_admin.username ?? ""}</span>
        </span>
      ) : (
        <span className="muted">None</span>
      ),
  },
  { key: "campuses", header: "Campuses", align: "right", render: (o) => o.campus_count },
  { key: "cameras", header: "Cameras", align: "right", render: (o) => o.camera_count },
  { key: "status", header: "Status", render: (o) => <StatusBadge status={o.status} /> },
  { key: "created", header: "Registered", render: (o) => formatDateTime(o.created_at) },
  {
    key: "view",
    header: <span className="sr-only">Actions</span>,
    render: (o) => (
      <Link className="btn btn-ghost btn-sm" to={`/organizations/${encodeURIComponent(o.organization_id)}`}>
        View details<span className="sr-only">: {o.name}</span>
      </Link>
    ),
  },
];

/** Organization Management (page 3): the directory of tenant organizations, metadata only. */
export function OrganizationsPage() {
  const toast = useToast();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<OrganizationStatus | "">("");
  const [sort, setSort] = useState<OrganizationSort>("created_at");
  const [order, setOrder] = useState<SortOrder>("desc");
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(text.trim());
      setOffset(0);
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const query: OrganizationQuery = { q: q || undefined, status: status || undefined, sort, order, limit: PAGE_SIZE, offset };
  const page = useAsync(() => platform.organizations(query), [q, status, sort, order, offset]);
  const deleted = useAsync(() => (showDeleted ? platform.deletedOrganizations() : Promise.resolve(null)), [showDeleted]);

  async function exportCsv() {
    setExporting(true);
    try {
      await platform.exportOrganizations(query);
      toast("success", "Organization directory exported.");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <section>
      <PageHeader
        title="Organizations"
        description="Every organization on the platform, with its official contact, Main Admin and counts. No camera feeds or organization records."
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={page.reload} loading={page.loading && Boolean(page.data)}>
              Refresh
            </Button>
            <Button variant="secondary" icon={Download} onClick={exportCsv} loading={exporting}>
              Export CSV
            </Button>
          </>
        }
      />
      <div className="filters" role="search" aria-label="Find organizations">
        <input
          type="search"
          aria-label="Search"
          placeholder="Name, official email or Organization ID"
          value={text}
          maxLength={200}
          onChange={(e) => setText(e.target.value)}
        />
        <select
          aria-label="Status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as OrganizationStatus | "");
            setOffset(0);
          }}
        >
          <option value="">All statuses</option>
          <option value="ACTIVE">Active</option>
          <option value="INACTIVE">Inactive</option>
          <option value="SUSPENDED">Suspended</option>
        </select>
        <select
          aria-label="Sort by"
          value={sort}
          onChange={(e) => {
            setSort(e.target.value as OrganizationSort);
            setOffset(0);
          }}
        >
          {SORTS.map(([value, label]) => (
            <option key={value} value={value}>
              Sort: {label}
            </option>
          ))}
        </select>
        <select aria-label="Order" value={order} onChange={(e) => setOrder(e.target.value as SortOrder)}>
          <option value="desc">Descending</option>
          <option value="asc">Ascending</option>
        </select>
      </div>
      {page.error ? (
        <ErrorState error={page.error} onRetry={page.reload} />
      ) : (
        <>
          <DataTable
            caption="Organizations"
            columns={COLUMNS}
            rows={page.data?.items ?? []}
            rowKey={(o) => o.organization_id}
            loading={page.loading && !page.data}
            empty={q || status ? "No organizations match." : "No organizations yet."}
          />
          {page.data && page.data.total > 0 && (
            <Pagination total={page.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
          )}
        </>
      )}
      <section className="card deleted-orgs" aria-labelledby="deleted-title">
        <div className="card-head">
          <h2 id="deleted-title">
            <Archive className="icon inline-icon" aria-hidden="true" /> Deleted organizations
          </h2>
          <Button variant="ghost" size="sm" aria-expanded={showDeleted} onClick={() => setShowDeleted(!showDeleted)}>
            {showDeleted ? "Hide" : "Show"}
          </Button>
        </div>
        <p className="muted small">Only the names of permanently erased organizations are kept; nothing else remains.</p>
        {showDeleted &&
          (deleted.error ? (
            <ErrorState error={deleted.error} onRetry={deleted.reload} />
          ) : !deleted.data ? (
            <p className="muted">Loading…</p>
          ) : deleted.data.organization_names.length === 0 ? (
            <p className="muted">No organization has been deleted.</p>
          ) : (
            <ul className="plain deleted-list" aria-label="Deleted organization names">
              {deleted.data.organization_names.map((name, i) => (
                <li key={`${name}-${i}`}>{name}</li>
              ))}
            </ul>
          ))}
      </section>
    </section>
  );
}
