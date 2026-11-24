import { Plus, Search, Upload } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../../../auth/AuthContext";
import { canManagePersonnel } from "../../../auth/permissions";
import { Button, DataTable, ErrorState, PageHeader, Pagination, StatusBadge, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { personnel, type DirectoryQuery } from "../api";
import type { DirectoryRow } from "../types";

const PAGE = 25;

export function DirectoryPage() {
  const { session } = useAuth();
  const admin = canManagePersonnel(session?.role);
  const [draft, setDraft] = useState<DirectoryQuery>({ q: "", category_id: "", status: "ACTIVE" });
  const [query, setQuery] = useState<DirectoryQuery>({ status: "ACTIVE", limit: PAGE, offset: 0 });
  const categories = useAsync(() => personnel.categories(), []);
  const directory = useAsync(() => personnel.directory(query), [query]);

  function search(event: FormEvent) {
    event.preventDefault();
    setQuery({ ...draft, limit: PAGE, offset: 0 });
  }

  const columns: Column<DirectoryRow>[] = [
    { key: "id", header: "Personnel ID", render: (p) => <span className="mono">{p.personnel_id}</span> },
    { key: "name", header: "Name", render: (p) => <Link to={`/personnel/${p.id}`}>{p.full_name}</Link> },
    { key: "external", header: "Institutional ID", render: (p) => p.external_id ?? "—" },
    { key: "category", header: "Category", render: (p) => p.category.label },
    { key: "department", header: "Department", render: (p) => p.department ?? "—" },
    { key: "designation", header: "Designation", render: (p) => p.designation ?? "—" },
    // Contact details are returned to Admins only; the column is not shown to Supervisors at all.
    ...(admin ? [{ key: "email", header: "Email", render: (p: DirectoryRow) => p.email ?? "—" }] : []),
    { key: "status", header: "Status", render: (p) => <StatusBadge status={p.status} /> },
  ];

  return (
    <section>
      <PageHeader
        title="Personnel Directory"
        description={admin ? undefined : "Read-only: basic details of this campus's personnel. Contact details are not shown."}
        actions={
          admin && (
            <>
              <Link className="button" to="/personnel/new">
                <Plus className="icon" aria-hidden="true" />
                Add person
              </Link>
              <Link className="button secondary" to="/personnel/imports/new">
                <Upload className="icon" aria-hidden="true" />
                Bulk import
              </Link>
            </>
          )
        }
      />
      <form className="filters" onSubmit={search} role="search">
        <input
          aria-label="Search"
          placeholder={admin ? "Name, Personnel ID, Institutional ID or email" : "Name, Personnel ID or Institutional ID"}
          value={draft.q ?? ""}
          onChange={(e) => setDraft({ ...draft, q: e.target.value })}
        />
        <select aria-label="Category" value={draft.category_id ?? ""} onChange={(e) => setDraft({ ...draft, category_id: e.target.value })}>
          <option value="">All categories</option>
          {(categories.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
        <select aria-label="Status" value={draft.status ?? ""} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
          <option value="">Any status</option>
          <option value="ACTIVE">Active</option>
          <option value="INACTIVE">Inactive</option>
        </select>
        <input aria-label="Department" placeholder="Department" value={draft.department ?? ""} onChange={(e) => setDraft({ ...draft, department: e.target.value })} />
        <input aria-label="Designation" placeholder="Designation" value={draft.designation ?? ""} onChange={(e) => setDraft({ ...draft, designation: e.target.value })} />
        <Button type="submit" icon={Search}>
          Search
        </Button>
      </form>
      {directory.error ? (
        <ErrorState error={directory.error} onRetry={directory.reload} />
      ) : (
        <>
          <DataTable
            caption="Personnel"
            columns={columns}
            rows={directory.data?.items ?? []}
            rowKey={(p) => p.id}
            loading={directory.loading && !directory.data}
            empty="No personnel found."
          />
          {directory.data && (
            <Pagination
              total={directory.data.total}
              limit={PAGE}
              offset={query.offset ?? 0}
              onChange={(offset) => setQuery({ ...query, offset })}
            />
          )}
        </>
      )}
    </section>
  );
}
