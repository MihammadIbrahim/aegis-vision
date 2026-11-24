import { Download, RotateCw, Search, Trash2, UserPlus } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { Button, DataTable, Dialog, ErrorState, Field, FormDialog, LoadingState, PageHeader, Pagination, PasswordInput, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { userRecords, EVENT_LABELS, type UserRecord } from "../api";

const PAGE = 50;

function RejoinDialog({ record, onClose, onDone }: { record: UserRecord; onClose: () => void; onDone: () => void }) {
  const tree = useAsync(() => locations.tree(), []);
  const [form, setForm] = useState({ full_name: record.full_name, username: record.username ?? "", phone_number: record.phone_number ?? "", new_password: "", current_password: "" });
  const [place, setPlace] = useState<LocationValue>(NO_LOCATION);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  return (
    <FormDialog
      title={`Rejoin ${record.full_name} (${record.display_id})`}
      submitLabel="Reactivate account"
      disabled={!form.current_password || form.full_name.trim().length < 2}
      onClose={onClose}
      onSubmit={async () => {
        await userRecords.rejoin(record.user_id, {
          full_name: form.full_name.trim(),
          username: form.username.trim() || null,
          phone_number: form.phone_number.trim() || null,
          new_password: form.new_password || null,
          location: record.role === "GUARD" && place.building_id ? { building_id: place.building_id, floor_id: place.floor_id || null, area_id: place.area_id || null } : null,
          current_password: form.current_password,
        });
        onDone();
      }}
    >
      <p className="dialog-message">The same account returns with its ID and history; no duplicate is created.</p>
      <div className="form-grid">
        <Field label="Full name" required>
          <input value={form.full_name} maxLength={120} onChange={set("full_name")} />
        </Field>
        <Field label="Username">
          <input value={form.username} maxLength={64} onChange={set("username")} />
        </Field>
        <Field label="Phone number">
          <input type="tel" value={form.phone_number} maxLength={40} onChange={set("phone_number")} />
        </Field>
        <Field label="New password" hint="Optional: set a new password for the returning user.">
          <PasswordInput value={form.new_password} autoComplete="new-password" onChange={set("new_password")} />
        </Field>
      </div>
      {record.role === "GUARD" && (
        <fieldset className="stack">
          <legend className="small">Location (none = Standby)</legend>
          <div className="row picker-row">
            <LocationPicker tree={tree.data ?? []} value={place} onChange={setPlace} anyBuildingLabel="Standby (no location)" idPrefix="rejoin" />
          </div>
        </fieldset>
      )}
      <Field label="Your current password" required hint="No code is needed for rejoining.">
        <PasswordInput value={form.current_password} autoComplete="current-password" onChange={set("current_password")} />
      </Field>
    </FormDialog>
  );
}

function DeleteDialog({ record, onClose, onDone }: { record: UserRecord; onClose: () => void; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  return (
    <FormDialog
      title={`Permanently delete ${record.full_name}'s data`}
      submitLabel="Delete permanently"
      disabled={!password || confirmation !== "DELETE"}
      onClose={onClose}
      onSubmit={async () => {
        await userRecords.remove(record.user_id, password, confirmation);
        onDone();
      }}
    >
      <p className="dialog-message">
        Name, username, email, phone, password and location are erased, and the name is replaced by “Deleted user” in reports, chats, cases
        and evidence. The account ID stays so alerts, cases and audit records remain linked. This cannot be undone.
      </p>
      <Field label='Type "DELETE" to confirm' required>
        <input value={confirmation} maxLength={20} onChange={(e) => setConfirmation(e.target.value)} />
      </Field>
      <Field label="Your current password" required>
        <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
    </FormDialog>
  );
}

function DetailDialog({ userId, onClose }: { userId: string; onClose: () => void }) {
  const detail = useAsync(() => userRecords.detail(userId), [userId]);
  return (
    <Dialog open title="User record" onClose={onClose}>
      {detail.error ? (
        <ErrorState error={detail.error} onRetry={detail.reload} />
      ) : !detail.data ? (
        <LoadingState />
      ) : (
        <div className="stack">
          <dl className="detail-list">
            <div><dt>ID</dt><dd className="mono">{detail.data.record.display_id}</dd></div>
            <div><dt>Name</dt><dd>{detail.data.record.full_name}</dd></div>
            <div><dt>Role</dt><dd>{detail.data.record.role === "GUARD" ? "Guard" : "Supervisor"}</dd></div>
            <div><dt>Status</dt><dd><StatusBadge status={detail.data.record.status} /></dd></div>
            <div><dt>Email</dt><dd className="break-anywhere">{detail.data.record.email ?? "—"}</dd></div>
            <div><dt>Phone</dt><dd>{detail.data.record.phone_number ?? "—"}</dd></div>
            <div><dt>Location</dt><dd>{detail.data.record.location ?? "—"}</dd></div>
          </dl>
          <h3 className="dash-subhead">Status history</h3>
          <ol className="plain stack small">
            {detail.data.history.map((h, n) => (
              <li key={`${h.changed_at}-${n}`}>
                {formatDateTime(h.changed_at)} · {EVENT_LABELS[h.event] ?? h.event}
                {h.changed_by_name ? ` by ${h.changed_by_name}` : ""}
              </li>
            ))}
          </ol>
          <h3 className="dash-subhead">Recent activity</h3>
          {detail.data.activity.length === 0 ? (
            <p className="muted small">No recorded activity.</p>
          ) : (
            <ol className="plain stack small">
              {detail.data.activity.map((a, n) => (
                <li key={`${a.occurred_at}-${n}`}>
                  {formatDateTime(a.occurred_at)} · {a.action.replace(/_/g, " ").toLowerCase()} {a.by_user ? "(by this user)" : "(about this user)"}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Dialog>
  );
}

/** User Records & Rejoining (Org Admin page 13): Supervisors and Guards of the campus, current and former. */
export function UserRecordsPage() {
  const toast = useToast();
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [deleted, setDeleted] = useState(false);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [lookupId, setLookupId] = useState("");
  const [rejoining, setRejoining] = useState<UserRecord | null>(null);
  const [removing, setRemoving] = useState<UserRecord | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(text.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [text]);
  const page = useAsync(
    () => userRecords.list({ role: role || undefined, status: status || undefined, q: q || undefined, include_deleted: deleted, limit: PAGE, offset }),
    [role, status, q, deleted, offset],
  );
  const fail = (e: unknown) => toast("error", e instanceof Error ? e.message : String(e));
  const done = (text: string) => () => {
    setRejoining(null);
    setRemoving(null);
    toast("success", text);
    page.reload();
  };

  async function lookup(event: FormEvent) {
    event.preventDefault();
    if (!lookupId.trim()) return;
    try {
      const found = await userRecords.lookup(lookupId.trim());
      if (found.status === "ACTIVE") toast("info", `${found.full_name} is already active.`);
      else if (found.deleted) toast("error", "This person's data was deleted; add them as new staff.");
      else setRejoining(found);
    } catch (e) {
      fail(e);
    }
  }

  const columns: Column<UserRecord>[] = [
    {
      key: "who",
      header: "User",
      render: (r) => (
        <div className="stack gap-0">
          <button type="button" className="link" onClick={() => setViewing(r.user_id)}>
            {r.full_name}
          </button>
          <span className="muted small mono">{r.display_id}</span>
        </div>
      ),
    },
    { key: "role", header: "Role", render: (r) => (r.role === "GUARD" ? "Guard" : "Supervisor") },
    { key: "status", header: "Status", render: (r) => (r.deleted ? <span className="muted">Data deleted</span> : <StatusBadge status={r.status} />) },
    { key: "contact", header: "Contact", render: (r) => <span className="break-anywhere">{r.email ?? "—"}{r.phone_number ? ` · ${r.phone_number}` : ""}</span> },
    { key: "location", header: "Location", render: (r) => r.location ?? (r.role === "GUARD" ? "Standby" : "—") },
    { key: "since", header: "Status since", render: (r) => formatDateTime(r.status_changed_at ?? r.created_at) },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      render: (r) =>
        r.status === "LEFT" && !r.deleted ? (
          <div className="actions">
            <Button size="sm" variant="secondary" icon={UserPlus} onClick={() => setRejoining(r)}>
              Rejoin
            </Button>
            <Button size="sm" variant="danger" icon={Trash2} onClick={() => setRemoving(r)}>
              Delete data
            </Button>
          </div>
        ) : null,
    },
  ];

  return (
    <section>
      <PageHeader
        title="User Records & Rejoining"
        description="Supervisors and Guards of this campus, current and former. Former staff can rejoin with the same account; their data can be deleted permanently."
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={page.reload}>
              Refresh
            </Button>
            <Button variant="secondary" icon={Download} onClick={() => userRecords.exportCsv({ role: role || undefined, status: status || undefined, q: q || undefined, include_deleted: deleted }).catch(fail)}>
              Download CSV
            </Button>
          </>
        }
      />
      <form className="filters" onSubmit={lookup} aria-label="Rejoin by ID">
        <input aria-label="Existing user ID" placeholder="Rejoin by existing Guard or Supervisor ID" value={lookupId} maxLength={40} onChange={(e) => setLookupId(e.target.value)} />
        <Button type="submit" variant="secondary" icon={Search} disabled={!lookupId.trim()}>
          Find for rejoining
        </Button>
      </form>
      <div className="filters" role="search" aria-label="Filter records">
        <input type="search" aria-label="Search" placeholder="ID, name, username or email" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
        <select aria-label="Role" value={role} onChange={(e) => { setRole(e.target.value); setOffset(0); }}>
          <option value="">Supervisors and Guards</option>
          <option value="SUPERVISOR">Supervisors</option>
          <option value="GUARD">Guards</option>
        </select>
        <select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          <option value="">Active and left</option>
          <option value="ACTIVE">Active</option>
          <option value="LEFT">Left</option>
        </select>
        <label className="checkbox small">
          <input type="checkbox" checked={deleted} onChange={(e) => setDeleted(e.target.checked)} /> Include deleted data
        </label>
      </div>
      {page.error ? (
        <ErrorState error={page.error} onRetry={page.reload} />
      ) : (
        <>
          <DataTable caption="User records" columns={columns} rows={page.data?.items ?? []} rowKey={(r) => r.user_id} loading={page.loading && !page.data} empty="No records match." />
          {page.data && <Pagination offset={offset} limit={PAGE} total={page.data.total} onChange={setOffset} />}
        </>
      )}
      {rejoining && <RejoinDialog record={rejoining} onClose={() => setRejoining(null)} onDone={done(`${rejoining.full_name} is active again.`)} />}
      {removing && <DeleteDialog record={removing} onClose={() => setRemoving(null)} onDone={done("The personal data was deleted.")} />}
      {viewing && <DetailDialog userId={viewing} onClose={() => setViewing(null)} />}
    </section>
  );
}
