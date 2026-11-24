import { CalendarClock, History, ShieldOff } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button, DataTable, Dialog, Field, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatDateTime, formatLocationPath } from "../../../lib/format";
import { access } from "../api";
import type { Grant } from "../types";
import { DAYS, isoToLocal, localToIso } from "./shared";

function ValidityDialog({ grant, onClose, onSaved }: { grant: Grant; onClose: () => void; onSaved: () => void }) {
  const [until, setUntil] = useState(isoToLocal(grant.valid_until));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // The same body as before: only the end of validity changes; the rest is sent back as it was.
      await access.update(grant.id, { valid_from: grant.valid_from, valid_until: localToIso(until), schedule: grant.schedule, note: grant.note });
      toast("success", "Validity updated.");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open title={`Change validity: ${grant.area.name}`} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <p className="dialog-message">
          {grant.personnel.full_name} · valid from {formatDateTime(grant.valid_from)}
        </p>
        <Field label="Valid until" hint="Leave empty to keep access until it is revoked." error={error}>
          <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
        </Field>
        <div className="dialog-foot">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            Save validity
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Grants of a person or an area. Admins get change / revoke / history; Supervisors read only. */
export function GrantList({ grants, admin, onChange, caption }: { grants: Grant[]; admin: boolean; onChange: () => void; caption: string }) {
  const toast = useToast();
  const { confirm, dialog } = useConfirm();
  const [editing, setEditing] = useState<Grant | null>(null);
  const [history, setHistory] = useState<Record<string, string[]>>({});

  async function revoke(g: Grant) {
    const answer = await confirm({
      title: `Revoke access to ${g.area.name}?`,
      message: `${g.personnel.full_name} will no longer be authorized for this area. The permission is kept as history.`,
      confirmLabel: "Revoke access",
      tone: "danger",
      reason: { label: "Reason", maxLength: 500 },
    });
    if (!answer.confirmed) return;
    try {
      await access.revoke(g.id, answer.reason);
      toast("success", "Access revoked.");
      onChange();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  async function showHistory(g: Grant) {
    try {
      const h = await access.history(g.id);
      setHistory({ ...history, [g.id]: h.history.map((e) => `${formatDateTime(e.occurred_at)}: ${e.action} by ${e.actor_name ?? "—"}`) });
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  const columns: Column<Grant>[] = [
    {
      key: "person",
      header: "Person",
      render: (g) => (
        <>
          {g.personnel.full_name}
          <div className="small mono">{g.personnel.personnel_id}</div>
        </>
      ),
    },
    {
      key: "area",
      header: "Area",
      render: (g) => (
        <>
          {g.area.name}
          <div className="small muted">{formatLocationPath(g.area.building, g.area.floor)}</div>
        </>
      ),
    },
    {
      key: "valid",
      header: "Valid",
      className: "small",
      render: (g) => `${formatDateTime(g.valid_from)} → ${g.valid_until ? formatDateTime(g.valid_until) : "until revoked"}`,
    },
    {
      key: "schedule",
      header: "Schedule",
      className: "small",
      render: (g) =>
        g.schedule ? `${g.schedule.days.map((d) => DAYS[d - 1]).join(", ")} ${g.schedule.start}–${g.schedule.end} (${g.schedule.timezone})` : "Any time",
    },
    {
      key: "status",
      header: "Status",
      render: (g) => (
        <>
          <StatusBadge status={g.status} />
          {g.revoke_reason && <div className="small muted">{g.revoke_reason}</div>}
        </>
      ),
    },
  ];
  if (admin) {
    columns.push({
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      render: (g) => (
        <div className="stack">
          <div className="actions">
            {g.status === "ACTIVE" && (
              <>
                <Button variant="ghost" size="sm" icon={CalendarClock} onClick={() => setEditing(g)}>
                  Change validity
                </Button>
                <Button variant="ghost" size="sm" icon={ShieldOff} onClick={() => void revoke(g)}>
                  Revoke
                </Button>
              </>
            )}
            <Button variant="ghost" size="sm" icon={History} onClick={() => void showHistory(g)}>
              History
            </Button>
          </div>
          {history[g.id] && (
            <ul className="plain small">
              {history[g.id].map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </div>
      ),
    });
  }

  return (
    <>
      <DataTable caption={caption} columns={columns} rows={grants} rowKey={(g) => g.id} empty="No access permissions." />
      {editing && (
        <ValidityDialog
          grant={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            onChange();
          }}
        />
      )}
      {dialog}
    </>
  );
}
