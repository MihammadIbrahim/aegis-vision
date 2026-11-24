import { Link2, RotateCw, Send, Unlink } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Button, Dialog, ErrorState, Field, FormDialog, LoadingState, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { statusLook } from "../../../lib/status";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { alerts } from "../../alerts";
import { manualReports } from "../../manual-reports";
import { evidence } from "../../video";
import { CLOSED, incidents, STATUS_HELP, STATUS_ORDER, type Incident, type IncidentStatus, type LinkKind } from "../api";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Move the case to another status; resolving, dismissing and reopening need a note. */
export function StatusDialog({ incident, onClose, onSaved }: { incident: Incident; onClose: () => void; onSaved: () => void }) {
  const closed = CLOSED.includes(incident.status);
  const options = closed ? (["UNDER_INVESTIGATION"] as IncidentStatus[]) : STATUS_ORDER.filter((s) => s !== incident.status);
  const [target, setTarget] = useState<IncidentStatus>(options[0]);
  const [note, setNote] = useState("");
  const needsNote = closed || CLOSED.includes(target);
  return (
    <FormDialog
      title={closed ? `Reopen ${incident.reference}` : `Change the status of ${incident.reference}`}
      submitLabel={closed ? "Reopen" : "Change status"}
      disabled={needsNote && !note.trim()}
      onClose={onClose}
      onSubmit={async () => {
        await incidents.setStatus(incident.id, target, note.trim() || null);
        onSaved();
      }}
    >
      <Field label="New status" required hint={STATUS_HELP[target]}>
        <select value={target} onChange={(e) => setTarget(e.target.value as IncidentStatus)}>
          {options.map((s) => (
            <option key={s} value={s}>
              {statusLook(s).label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Note" required={needsNote} hint={needsNote ? "Required: why the case is closed or reopened." : "Optional"}>
        <textarea value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </FormDialog>
  );
}

/** Assign an Admin or Supervisor of the campus (they are notified), or clear the assignment. */
export function AssignDialog({ incident, onClose, onSaved }: { incident: Incident; onClose: () => void; onSaved: () => void }) {
  const staff = useAsync(() => incidents.staff(), []);
  const [user, setUser] = useState(incident.assigned_to ?? "");
  return (
    <FormDialog
      title={`Assign ${incident.reference}`}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={async () => {
        await incidents.assign(incident.id, user || null);
        onSaved();
      }}
    >
      <Field label="Responsible Admin or Supervisor">
        <select value={user} onChange={(e) => setUser(e.target.value)}>
          <option value="">Not assigned</option>
          {(staff.data ?? []).map((s) => (
            <option key={s.user_id} value={s.user_id}>
              {s.full_name} ({s.role === "ADMIN" ? "Admin" : "Supervisor"}) · {s.display_id}
            </option>
          ))}
        </select>
      </Field>
    </FormDialog>
  );
}

interface Candidate {
  id: string;
  label: string;
  detail: string;
}

/** Pick alerts, manual reports or evidence of the campus to link to the case. */
export function LinkDialog({
  incident,
  kind,
  linked,
  onClose,
  onSaved,
}: {
  incident: Incident;
  kind: LinkKind;
  linked: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const options = useAsync<Candidate[]>(async () => {
    if (kind === "alerts") {
      return (await alerts.list({ limit: 100, offset: 0 })).map((a) => ({
        id: a.id,
        label: `${statusLook(a.alert_type).label} · ${a.camera_name}`,
        detail: `${formatDateTime(a.detected_at)} · ${statusLook(a.status).label}`,
      }));
    }
    if (kind === "reports") {
      return (await manualReports.list(100, 0)).map((r) => ({
        id: r.id,
        label: `${r.report_type} · ${r.guard_name}`,
        detail: `${formatDateTime(r.created_at)} · ${r.building_name}`,
      }));
    }
    return (await evidence.list({ limit: 100 })).map((e) => ({
      id: e.id,
      label: `${e.title} · ${e.camera_name}`,
      detail: `${formatDateTime(e.covers_from)}${e.incident_id ? " · linked to another case" : ""}`,
    }));
  }, [kind]);
  const title = { alerts: "Link alerts", reports: "Link manual reports", evidence: "Link evidence" }[kind];
  const available = (options.data ?? []).filter((o) => !linked.includes(o.id));
  return (
    <Dialog open title={`${title} to ${incident.reference}`} onClose={onClose}>
      {options.error ? (
        <ErrorState error={options.error} onRetry={options.reload} />
      ) : options.loading && !options.data ? (
        <LoadingState />
      ) : available.length === 0 ? (
        <p className="muted">Nothing left to link.</p>
      ) : (
        <ul className="plain stack" aria-label="Candidates">
          {available.map((o) => (
            <li key={o.id} className="row">
              <div className="stack gap-0 flex-1 min-w-0">
                <span className="truncate">{o.label}</span>
                <span className="muted small">{o.detail}</span>
              </div>
              <Button
                size="sm"
                variant="secondary"
                icon={Link2}
                loading={busy === o.id}
                onClick={async () => {
                  setBusy(o.id);
                  try {
                    await incidents.link(incident.id, kind, o.id);
                    toast("success", "Linked to the case.");
                    onSaved();
                  } catch (e) {
                    toast("error", message(e));
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                Link
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="dialog-foot">
        <Button variant="secondary" onClick={onClose}>
          Done
        </Button>
      </div>
    </Dialog>
  );
}

export function UnlinkButton({ incident, kind, id, onDone }: { incident: Incident; kind: LinkKind; id: string; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (CLOSED.includes(incident.status)) return null;
  return (
    <Button
      size="sm"
      variant="ghost"
      icon={Unlink}
      loading={busy}
      aria-label="Unlink"
      onClick={async () => {
        setBusy(true);
        try {
          await incidents.unlink(incident.id, kind, id);
          onDone();
        } catch (e) {
          toast("error", message(e));
        } finally {
          setBusy(false);
        }
      }}
    >
      Unlink
    </Button>
  );
}

/** The case chat with staff and the Guards taking part (15.5). New messages arrive live through `incident.message`
 * signals (decision D-026); without the WebSocket the thread is read every 10 s. */
export function IncidentChat({ incident }: { incident: Incident }) {
  const toast = useToast();
  const thread = useAsync(() => incidents.messages(incident.id), [incident.id]);
  useLiveRefresh(thread.reload, (e) => e.event === "incident.message" && e.incident_id === incident.id, {
    debounceMs: 150,
    fallbackMs: 10_000,
  });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const closed = CLOSED.includes(incident.status);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    try {
      await incidents.post(incident.id, text.trim());
      setText("");
      thread.reload();
    } catch (e) {
      toast("error", message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="case-chat">
      <div className="card-head">
        <h2 id="case-chat">Case chat</h2>
        <Button variant="ghost" size="sm" icon={RotateCw} onClick={thread.reload}>
          Refresh
        </Button>
      </div>
      {thread.error ? (
        <ErrorState error={thread.error} onRetry={thread.reload} />
      ) : thread.loading && !thread.data ? (
        <LoadingState />
      ) : (thread.data ?? []).length === 0 ? (
        <p className="muted">No messages yet.</p>
      ) : (
        <ol className="plain chat" aria-label="Messages">
          {(thread.data ?? []).map((m) => (
            <li key={m.id} className={`chat-message${m.sender_role === "GUARD" ? "" : " is-staff"}`}>
              <p className="chat-meta">
                <strong>{m.sender_name}</strong> <span className="muted">· {m.sender_role.toLowerCase()}</span>{" "}
                <time className="muted small" dateTime={m.created_at}>
                  {formatDateTime(m.created_at)}
                </time>
              </p>
              <p className="chat-body">{m.body}</p>
            </li>
          ))}
        </ol>
      )}
      {closed ? (
        <p className="muted small">The case is closed, so no new messages can be posted.</p>
      ) : (
        <form className="stack" onSubmit={send}>
          <Field label="Message" required hint="Seen by this campus's Admins and Supervisors and the Guards taking part.">
            <textarea value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} />
          </Field>
          <div className="actions">
            <Button type="submit" icon={Send} loading={busy} disabled={!text.trim()}>
              Send message
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}

export function AlertTypeBadge({ type }: { type: string }) {
  return <StatusBadge status={type} />;
}

export function AlertLink({ id, children }: { id: string; children: ReactNode }) {
  return <Link to={`/alerts/${id}`}>{children}</Link>;
}
