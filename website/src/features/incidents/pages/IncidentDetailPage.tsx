import { ArrowLeft, Bot, Pencil, Plus, RotateCw, ShieldAlert, UserCheck, UserPlus, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import {
  Button,
  DataTable,
  ErrorState,
  Field,
  FormDialog,
  LoadingState,
  PageHeader,
  StatusBadge,
  useToast,
  type Column,
} from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { InvestigatePanel } from "../../assistant/components/InvestigatePanel";
import { CallControls } from "../../calls/components/CallViews";
import { formatDateTime } from "../../../lib/format";
import { statusLook } from "../../../lib/status";
import { staff } from "../../staff";
import {
  CLOSED,
  EVENT_TEXT,
  GAP_TEXT,
  incidents,
  type LinkedAlert,
  type LinkedEvidence,
  type LinkedReport,
  type LinkKind,
  type Participant,
} from "../api";
import { IncidentForm } from "../components/IncidentForm";
import { AssignDialog, IncidentChat, LinkDialog, StatusDialog, UnlinkButton } from "../components/IncidentParts";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** One case: details, linked alerts, reports and evidence, verified person-search results, the limits of
 * the footage, notes, the full history and the case chat (Org Admin page 6, Supervisor page 6). */
export function IncidentDetailPage() {
  const { id = "" } = useParams();
  const toast = useToast();
  const detail = useAsync(() => incidents.get(id), [id]);
  // Notes, status, links, assignment and invitations by anyone arrive as `incident.updated` signals (D-026).
  useLiveRefresh(detail.reload, (e) => e.event === "incident.updated" && e.incident_id === id, { fallbackMs: 20_000 });
  const [dialog, setDialog] = useState<"edit" | "status" | "assign" | LinkKind | "invite" | null>(null);
  const [note, setNote] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  const guards = useAsync(
    () => (dialog === "invite" ? staff.list({ role: "GUARD", status: "ACTIVE", limit: 200, offset: 0 }) : Promise.resolve([])),
    [dialog],
  );

  if (detail.error) return <ErrorState error={detail.error} onRetry={detail.reload} />;
  if (!detail.data) return <LoadingState />;
  const d = detail.data;
  const i = d.incident;
  const closed = CLOSED.includes(i.status);
  const done = () => {
    setDialog(null);
    detail.reload();
  };

  async function addNote(event: FormEvent) {
    event.preventDefault();
    if (!note.trim()) return;
    setSavingNote(true);
    try {
      await incidents.addNote(i.id, note.trim());
      setNote("");
      detail.reload();
    } catch (e) {
      toast("error", message(e));
    } finally {
      setSavingNote(false);
    }
  }

  const alertColumns: Column<LinkedAlert>[] = [
    { key: "type", header: "AI event", render: (a) => <Link to={`/alerts/${a.alert_id}`}><StatusBadge status={a.alert_type} /></Link> },
    { key: "camera", header: "Camera", render: (a) => `${a.camera_name}${a.location ? ` · ${a.location}` : ""}` },
    { key: "when", header: "Detected", render: (a) => `${formatDateTime(a.detected_at)}${a.detection_count > 1 ? ` (${a.detection_count} times)` : ""}` },
    { key: "confidence", header: "Confidence", align: "right", render: (a) => (a.confidence === null ? "—" : `${Math.round(a.confidence * 100)}%`) },
    { key: "status", header: "Alert status", render: (a) => <StatusBadge status={a.status} /> },
    { key: "unlink", header: <span className="sr-only">Unlink</span>, render: (a) => <UnlinkButton incident={i} kind="alerts" id={a.alert_id} onDone={detail.reload} /> },
  ];
  const reportColumns: Column<LinkedReport>[] = [
    { key: "type", header: "Report", render: (r) => <Link to={`/manual-reports/${r.report_id}`}>{r.report_type}</Link> },
    { key: "by", header: "Guard", render: (r) => `${r.reporter_name} · ${r.reporter_guard_id}` },
    { key: "where", header: "Location", render: (r) => r.location ?? "—" },
    { key: "when", header: "Submitted", render: (r) => formatDateTime(r.created_at) },
    { key: "unlink", header: <span className="sr-only">Unlink</span>, render: (r) => <UnlinkButton incident={i} kind="reports" id={r.report_id} onDone={detail.reload} /> },
  ];
  const evidenceColumns: Column<LinkedEvidence>[] = [
    { key: "title", header: "Evidence", render: (e) => <Link to={`/evidence/${e.evidence_id}`}>{e.title}</Link> },
    { key: "kind", header: "Type", render: (e) => <StatusBadge status={e.kind} /> },
    { key: "camera", header: "Camera", render: (e) => e.camera_name },
    { key: "covers", header: "Covers", render: (e) => formatDateTime(e.covers_from) },
    { key: "by", header: "Saved by", render: (e) => e.saved_by_name },
    { key: "unlink", header: <span className="sr-only">Unlink</span>, render: (e) => <UnlinkButton incident={i} kind="evidence" id={e.evidence_id} onDone={detail.reload} /> },
  ];

  return (
    <section>
      <Link to="/incidents" className="back-link">
        <ArrowLeft className="icon" aria-hidden="true" /> All cases
      </Link>
      <PageHeader
        title={`${i.reference} · ${i.title}`}
        description={`Opened by ${i.created_by_name} on ${formatDateTime(i.created_at)}`}
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={detail.reload}>
              Refresh
            </Button>
            {!closed && (
              <Button variant="secondary" icon={Pencil} onClick={() => setDialog("edit")}>
                Edit
              </Button>
            )}
            {!closed && (
              <Button variant="secondary" icon={UserCheck} onClick={() => setDialog("assign")}>
                Assign
              </Button>
            )}
            <Button onClick={() => setDialog("status")}>{closed ? "Reopen" : "Change status"}</Button>
          </>
        }
      />
      <div className="grid-2">
        <section className="card" aria-labelledby="case-overview">
          <h2 id="case-overview">Overview</h2>
          <dl className="detail-list">
            <div><dt>Status</dt><dd><StatusBadge status={i.status} /></dd></div>
            <div><dt>Priority</dt><dd><StatusBadge status={i.priority} /></dd></div>
            <div><dt>Happened</dt><dd>{formatDateTime(i.occurred_at)}</dd></div>
            <div><dt>Location</dt><dd>{i.location ?? "—"}</dd></div>
            <div><dt>Camera</dt><dd>{i.camera_name ?? "—"}</dd></div>
            <div><dt>Assigned to</dt><dd>{i.assigned_name ?? "Not assigned"}</dd></div>
            {i.closed_at && <div><dt>Closed</dt><dd>{formatDateTime(i.closed_at)}</dd></div>}
            {i.resolution_note && <div><dt>Closing note</dt><dd className="prewrap">{i.resolution_note}</dd></div>}
          </dl>
          {i.description && <p className="prewrap">{i.description}</p>}
        </section>
        <section className="card" aria-labelledby="case-limits">
          <h2 id="case-limits">
            <ShieldAlert className="inline-icon" aria-hidden="true" /> Evidence limits
          </h2>
          {d.footage_gaps.length === 0 ? (
            <p className="muted">
              {d.alerts.length || i.camera_name ? "Recordings exist around the case's moments." : "No camera is linked, so no footage is expected."}
            </p>
          ) : (
            <ul className="plain stack" aria-label="Missing or limited footage">
              {d.footage_gaps.map((g, n) => (
                <li key={`${g.camera_name}-${g.issue}-${n}`} className="notice warning">
                  <strong>{g.camera_name}</strong> ({formatDateTime(g.window_from)}): {GAP_TEXT[g.issue]}
                </li>
              ))}
            </ul>
          )}
          <p className="notice" role="note">
            <Bot className="inline-icon" aria-hidden="true" /> The AI investigation summary, policy retrieval and recommended
            next steps need the Investigation Assistant, which is not deployed. Every finding on this page is made by people;
            AI detections are signals for review, never conclusions about anyone.
          </p>
        </section>
      </div>

      <section className="card" aria-labelledby="case-alerts">
        <div className="card-head">
          <h2 id="case-alerts">Related AI events ({d.alerts.length})</h2>
          {!closed && (
            <Button size="sm" variant="secondary" icon={Plus} onClick={() => setDialog("alerts")}>
              Link alerts
            </Button>
          )}
        </div>
        <DataTable caption="Linked alerts" columns={alertColumns} rows={d.alerts} rowKey={(a) => a.alert_id} empty="No alert is linked." />
      </section>
      <section className="card" aria-labelledby="case-reports">
        <div className="card-head">
          <h2 id="case-reports">Manual reports ({d.reports.length})</h2>
          {!closed && (
            <Button size="sm" variant="secondary" icon={Plus} onClick={() => setDialog("reports")}>
              Link reports
            </Button>
          )}
        </div>
        <DataTable caption="Linked manual reports" columns={reportColumns} rows={d.reports} rowKey={(r) => r.report_id} empty="No manual report is linked." />
      </section>
      <section className="card" aria-labelledby="case-evidence">
        <div className="card-head">
          <h2 id="case-evidence">Supporting evidence ({d.evidence.length})</h2>
          {!closed && (
            <Button size="sm" variant="secondary" icon={Plus} onClick={() => setDialog("evidence")}>
              Link evidence
            </Button>
          )}
        </div>
        <DataTable caption="Linked evidence" columns={evidenceColumns} rows={d.evidence} rowKey={(e) => e.evidence_id} empty="No evidence is linked. Save clips from alerts, recordings or person search." />
      </section>
      <section className="card" aria-labelledby="case-person-search">
        <h2 id="case-person-search">Person-search results ({d.person_search.length})</h2>
        {d.person_search.length === 0 ? (
          <p className="muted">
            None linked. In <Link to="/person-search">Person Search</Link>, verify a likely match and link it to this case.
          </p>
        ) : (
          <ul className="plain stack">
            {d.person_search.map((p) => (
              <li key={p.result_id} className="row">
                <StatusBadge status={p.review} /> <span>{p.camera_name}{p.location ? ` · ${p.location}` : ""}</span>
                <span className="muted small">
                  {formatDateTime(p.observed_at)} · similarity {p.similarity.toFixed(2)} · reviewed by {p.reviewed_by_name ?? "—"}
                </span>
                <Link to={`/person-search/${p.search_id}`} className="small">
                  Open search
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid-2">
        <section className="card" aria-labelledby="case-people">
          <div className="card-head">
            <h2 id="case-people">Guards in the chat ({d.participants.length})</h2>
            {!closed && (
              <Button size="sm" variant="secondary" icon={UserPlus} onClick={() => setDialog("invite")}>
                Invite a Guard
              </Button>
            )}
          </div>
          {d.participants.length === 0 ? (
            <p className="muted">Guards who received a linked alert join automatically; you can invite others.</p>
          ) : (
            <ul className="plain stack">
              {d.participants.map((p: Participant) => (
                <li key={p.user_id} className="row">
                  <span>
                    {p.full_name} <span className="muted small mono">{p.display_id}</span>
                  </span>
                  <span className="muted small">{p.invited ? "Invited" : "Received a linked alert"}</span>
                  {p.invited && !closed && (
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={X}
                      onClick={() => incidents.uninvite(i.id, p.user_id).then(detail.reload, (e: unknown) => toast("error", message(e)))}
                    >
                      Remove
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card" aria-labelledby="case-notes">
          <h2 id="case-notes">Add an investigation note</h2>
          <form className="stack" onSubmit={addNote}>
            <Field label="Note" required hint="Kept in the case history; it cannot be edited or deleted.">
              <textarea value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
            </Field>
            <div className="actions">
              <Button type="submit" loading={savingNote} disabled={!note.trim()}>
                Add note
              </Button>
            </div>
          </form>
        </section>
      </div>

      <section className="card" aria-labelledby="case-history">
        <h2 id="case-history">Investigation history</h2>
        <ol className="plain stack" aria-label="History, oldest first">
          {d.history.map((e) => (
            <li key={e.id}>
              <time className="muted small" dateTime={e.occurred_at}>
                {formatDateTime(e.occurred_at)}
              </time>{" "}
              <strong>{e.actor_name}</strong> <span className="muted small">({e.actor_role.toLowerCase()})</span>{" "}
              {EVENT_TEXT[e.kind] ?? e.kind.toLowerCase()}
              {e.to_status && e.kind === "STATUS_CHANGED" && (
                <>
                  : {e.from_status ? `${statusLook(e.from_status).label} → ` : ""}
                  {statusLook(e.to_status).label}
                </>
              )}
              {typeof e.details.assignee === "string" && <>: {e.details.assignee}</>}
              {Array.isArray(e.details.changed) && <>: {(e.details.changed as string[]).join(", ")}</>}
              {e.note && <p className="prewrap small m-0">{e.note}</p>}
            </li>
          ))}
        </ol>
      </section>

      <InvestigatePanel incidentId={i.id} reference={i.reference} />
      <CallControls target={{ scope: "INCIDENT", id: i.id }} closed={closed} />
      <IncidentChat incident={i} />

      {dialog === "edit" && <IncidentForm incident={i} onClose={() => setDialog(null)} onSaved={done} />}
      {dialog === "status" && <StatusDialog incident={i} onClose={() => setDialog(null)} onSaved={done} />}
      {dialog === "assign" && <AssignDialog incident={i} onClose={() => setDialog(null)} onSaved={done} />}
      {(dialog === "alerts" || dialog === "reports" || dialog === "evidence") && (
        <LinkDialog
          incident={i}
          kind={dialog}
          linked={dialog === "alerts" ? d.alerts.map((a) => a.alert_id) : dialog === "reports" ? d.reports.map((r) => r.report_id) : d.evidence.map((e) => e.evidence_id)}
          onClose={done}
          onSaved={detail.reload}
        />
      )}
      {dialog === "invite" && (
        <LinkGuardDialog
          guards={(guards.data ?? []).filter((g) => !d.participants.some((p) => p.user_id === g.id))}
          loading={guards.loading}
          onClose={() => setDialog(null)}
          onInvite={async (guardId) => {
            await incidents.invite(i.id, guardId);
            toast("success", "The Guard was invited and notified.");
            done();
          }}
        />
      )}
    </section>
  );
}

function LinkGuardDialog({
  guards,
  loading,
  onClose,
  onInvite,
}: {
  guards: { id: string; display_id: string; full_name: string; category: string | null }[];
  loading: boolean;
  onClose: () => void;
  onInvite: (guardId: string) => Promise<void>;
}) {
  const [guard, setGuard] = useState("");
  const toast = useToast();
  return (
    <FormDialog
        title="Invite a Guard to the case chat"
        submitLabel="Invite"
        disabled={!guard}
        onClose={onClose}
        onSubmit={async () => {
          try {
            await onInvite(guard);
          } catch (e) {
            toast("error", message(e));
            throw e;
          }
        }}
      >
        <Field label="Guard" required hint={loading ? "Loading Guards…" : "Active Guards of this campus"}>
          <select value={guard} onChange={(e) => setGuard(e.target.value)}>
            <option value="">Choose a Guard</option>
            {guards.map((g) => (
              <option key={g.id} value={g.id}>
                {g.full_name} · {g.display_id} ({g.category === "STATIONED" ? "Stationed" : "Standby"})
              </option>
            ))}
          </select>
        </Field>
    </FormDialog>
  );
}
