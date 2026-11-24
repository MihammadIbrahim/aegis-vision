import { Plus, RotateCw, ShieldAlert, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useAuth } from "../../../auth/AuthContext";
import { canAdministerStaff } from "../../../auth/permissions";
import { Button, DataTable, ErrorState, PageHeader, Pagination, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatLocationPath } from "../../../lib/format";
import { staff } from "../api";
import { CreateStaffWizard } from "../components/CreateStaffWizard";
import { CATEGORY_TEXT, ROLE_LABEL } from "../labels";
import type { GuardCategory, StaffMember, StaffRole, StaffStatus } from "../types";

const PAGE = 25;
export const SEARCH_DELAY_MS = 300;

/** Guards (Admins and Supervisors) and Supervisors (Admins only) of this campus. */
export function StaffPage() {
  const { session } = useAuth();
  const admin = canAdministerStaff(session?.role);
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const role: StaffRole = admin && params.get("role") === "SUPERVISOR" ? "SUPERVISOR" : "GUARD";
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<GuardCategory | "">("");
  const [status, setStatus] = useState<StaffStatus | "">("ACTIVE");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(text.trim());
      setOffset(0);
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const list = useAsync(
    () =>
      staff.list({
        role,
        q: q || undefined,
        status: status || undefined,
        category: role === "GUARD" && category ? category : undefined,
        limit: PAGE + 1,
        offset,
      }),
    [role, q, status, category, offset],
  );
  const rows = (list.data ?? []).slice(0, PAGE);

  function switchRole(next: StaffRole) {
    setOffset(0);
    setCategory("");
    setParams(next === "SUPERVISOR" ? { role: "SUPERVISOR" } : {});
  }

  const columns: Column<StaffMember>[] = [
    {
      key: "name",
      header: "Name",
      render: (m) => (
        <Link to={`/staff/${m.id}`}>
          <strong>{m.full_name}</strong>
        </Link>
      ),
    },
    { key: "id", header: `${ROLE_LABEL[role]} ID`, render: (m) => <span className="mono small">{m.display_id}</span> },
    ...(role === "GUARD"
      ? [
          { key: "category", header: "Category", render: (m: StaffMember) => (m.category ? <StatusBadge status={m.category} /> : "—") },
          {
            key: "location",
            header: "Location",
            render: (m: StaffMember) =>
              m.location ? (
                formatLocationPath(m.location.building_name, m.location.floor_name, m.location.area_name)
              ) : (
                <span className="muted">Campus-wide (no post)</span>
              ),
          },
        ]
      : []),
    { key: "status", header: "Status", render: (m) => <StatusBadge status={m.status} /> },
    {
      key: "contact",
      header: "Contact",
      className: "break",
      render: (m) => (
        <span className="small">
          {m.email}
          {m.phone_number && <span className="muted block">{m.phone_number}</span>}
        </span>
      ),
    },
  ];

  return (
    <section>
      <PageHeader
        title="Staff Management"
        description={
          admin
            ? "Guards and Supervisors of this campus. Only Organization Admins add, deactivate or reactivate accounts."
            : "Guards of this campus. You can update Guard details, set passwords and change locations; only Organization Admins add or deactivate Guards."
        }
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={list.reload} loading={list.loading && Boolean(list.data)}>
              Refresh
            </Button>
            {admin && (
              <Button icon={Plus} onClick={() => setCreating(true)}>
                Add {ROLE_LABEL[role]}
              </Button>
            )}
          </>
        }
      />
      {admin && (
        <div className="tabs" role="tablist" aria-label="Staff type">
          {(["GUARD", "SUPERVISOR"] as const).map((r) => (
            <button
              key={r}
              type="button"
              role="tab"
              aria-selected={role === r}
              className={`tab${role === r ? " is-active" : ""}`}
              onClick={() => switchRole(r)}
            >
              {r === "GUARD" ? <ShieldAlert className="icon" aria-hidden="true" /> : <Users className="icon" aria-hidden="true" />}
              {ROLE_LABEL[r]}s
            </button>
          ))}
        </div>
      )}
      {role === "GUARD" && (
        <div className="category-legend" aria-label="Guard categories">
          {(["STANDBY", "STATIONED"] as const).map((c) => (
            <p key={c} className="small">
              <StatusBadge status={c} /> {CATEGORY_TEXT[c].description}
            </p>
          ))}
        </div>
      )}
      <div className="filters" role="search" aria-label={`Find ${ROLE_LABEL[role]}s`}>
        <input
          type="search"
          aria-label="Search"
          placeholder="Name, ID, username or email"
          value={text}
          maxLength={200}
          onChange={(e) => setText(e.target.value)}
        />
        {role === "GUARD" && (
          <select
            aria-label="Category"
            value={category}
            onChange={(e) => {
              setOffset(0);
              setCategory(e.target.value as GuardCategory | "");
            }}
          >
            <option value="">Standby and Stationed</option>
            <option value="STANDBY">Standby only</option>
            <option value="STATIONED">Stationed only</option>
          </select>
        )}
        <select
          aria-label="Status"
          value={status}
          onChange={(e) => {
            setOffset(0);
            setStatus(e.target.value as StaffStatus | "");
          }}
        >
          <option value="ACTIVE">Active</option>
          <option value="LEFT">Left (deactivated)</option>
          <option value="">Any status</option>
        </select>
      </div>
      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : (
        <>
          <DataTable
            caption={`${ROLE_LABEL[role]}s`}
            columns={columns}
            rows={rows}
            rowKey={(m) => m.id}
            loading={list.loading && !list.data}
            empty={q || category || status !== "ACTIVE" ? `No ${ROLE_LABEL[role]}s match.` : `No active ${ROLE_LABEL[role]}s yet.`}
          />
          {list.data && (offset > 0 || list.data.length > PAGE) && (
            <Pagination count={rows.length} hasMore={list.data.length > PAGE} limit={PAGE} offset={offset} onChange={setOffset} />
          )}
        </>
      )}
      {creating && (
        <CreateStaffWizard
          role={role}
          onClose={() => setCreating(false)}
          onCreated={(created) => {
            toast("success", `${created.full_name} added.`);
            list.reload();
          }}
        />
      )}
    </section>
  );
}
