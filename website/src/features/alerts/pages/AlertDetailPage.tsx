import { Archive, Ban, CircleArrowUp, CircleCheck, RotateCw } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";

import { useAuth } from "../../../auth/AuthContext";
import { canManageAlerts } from "../../../auth/permissions";
import { Button, DataTable, ErrorState, LoadingState, PageHeader, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatDateTime } from "../../../lib/format";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { CallControls } from "../../calls/components/CallViews";
import { alerts } from "../api";
import { AlertChat } from "../components/AlertChat";
import { AlertMediaCard, EscalationsCard, HandlerCard, LinkedCasesCard } from "../components/AlertOperations";
import { EscalateDialog } from "../components/EscalateDialog";
import { SaveEvidenceDialog } from "../../video";
import {
  alertLocation,
  ASSESSMENT_LABELS,
  eventDetails,
  eventLabel,
  formatConfidence,
  isClosed,
  REASON_LABELS,
  RECIPIENT_STATE_LABELS,
  ROLE_LABELS,
} from "../labels";
import type { Alert, AlertRecipient } from "../types";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children ?? "—"}</dd>
    </>
  );
}

const RECIPIENT_COLUMNS: Column<AlertRecipient>[] = [
  { key: "id", header: "Guard ID", render: (r) => <span className="mono">{r.guard_id}</span> },
  { key: "name", header: "Name", render: (r) => r.full_name },
  { key: "reason", header: "Why", render: (r) => REASON_LABELS[r.reason] ?? r.reason },
  { key: "state", header: "Progress", render: (r) => <StatusBadge status={r.state} label={RECIPIENT_STATE_LABELS[r.state]} /> },
  { key: "assessment", header: "Assessment", render: (r) => (r.assessment ? ASSESSMENT_LABELS[r.assessment] : "—") },
  { key: "ack", header: "Acknowledged", render: (r) => formatDateTime(r.acknowledged_at) },
  { key: "resp", header: "Responding since", render: (r) => formatDateTime(r.responding_at) },
];

export function AlertDetailPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const staff = canManageAlerts(session?.role);
  const toast = useToast();
  const { confirm, dialog } = useConfirm();
  const alert = useAsync(() => alerts.get(id), [id]);
  const history = useAsync(() => alerts.history(id), [id]);
  const [escalating, setEscalating] = useState(false);
  const [savingEvidence, setSavingEvidence] = useState(false);
  const [busy, setBusy] = useState(false);

  function refresh() {
    alert.reload();
    history.reload();
  }
  // Guard acknowledgements, assessments, "I'm Responding", escalations and status changes arrive as signals
  // (decision D-026); without the WebSocket the page polls every 15 s.
  useLiveRefresh(refresh, (e) => (e.event === "alert.updated" || e.event === "alert.assigned") && e.alert_id === id, {
    fallbackMs: 15_000,
  });

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await action();
      toast("success", done);
      refresh();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function resolve(a: Alert) {
    const answer = await confirm({
      title: `Resolve this alert?`,
      message: `${a.camera_name}: the alert is closed for everyone and cannot be reopened.`,
      confirmLabel: "Resolve alert",
      reason: { label: "Note", maxLength: 1000 },
    });
    if (answer.confirmed) await run(() => alerts.resolve(a.id, answer.reason), "Alert resolved.");
  }

  async function dismiss(a: Alert) {
    const answer = await confirm({
      title: `Dismiss this alert?`,
      message: `${a.camera_name}: use this for a false alarm. The alert is closed and cannot be reopened.`,
      confirmLabel: "Dismiss alert",
      tone: "danger",
      reason: { label: "Reason", required: true, maxLength: 1000 },
    });
    if (answer.confirmed && answer.reason) await run(() => alerts.dismiss(a.id, answer.reason as string), "Alert dismissed.");
  }

  if (!alert.data) return alert.error ? <ErrorState error={alert.error} onRetry={refresh} /> : <LoadingState />;
  const a = alert.data;
  const closed = isClosed(a);
  const outdoor = a.location.building_kind === "OUTDOOR_PERIMETER";

  return (
    <section>
      <PageHeader
        back={{ to: "/alerts", label: "Alerts" }}
        title={
          <>
            <StatusBadge status={a.alert_type} /> {a.camera_name}
          </>
        }
        description={
          <>
            <StatusBadge status={a.priority} /> <StatusBadge status={a.status} />
          </>
        }
        actions={
          <>
            <Button variant="ghost" icon={RotateCw} onClick={refresh}>
              Refresh
            </Button>
            {staff && (
              <Button variant="outline" icon={Archive} onClick={() => setSavingEvidence(true)}>
                Save as evidence
              </Button>
            )}
            {staff && !closed && (
              <>
                <Button icon={CircleCheck} loading={busy} onClick={() => void resolve(a)}>
                  Resolve
                </Button>
                <Button variant="secondary" icon={CircleArrowUp} disabled={busy} onClick={() => setEscalating(true)}>
                  Escalate
                </Button>
                <Button variant="danger" icon={Ban} disabled={busy} onClick={() => void dismiss(a)}>
                  Dismiss
                </Button>
              </>
            )}
          </>
        }
      />
      <p className="notice info">AI detections are signals for review, not proof. Confirm what is happening before acting.</p>
      <div className="grid-2">
        <section className="card" aria-labelledby="details-title">
          <h2 id="details-title">Details</h2>
          <dl>
            <Row label="Alert ID">
              <span className="mono">{a.id}</span>
            </Row>
            <Row label="Camera">{a.camera_name}</Row>
            <Row label="Location">
              {alertLocation(a)}
              {a.location.building_name && <span className="muted small"> ({outdoor ? "outdoor" : "indoor"})</span>}
            </Row>
            <Row label="Confidence">{formatConfidence(a.confidence)}</Row>
            <Row label="First detected">{formatDateTime(a.detected_at)}</Row>
            <Row label="Last detected">{formatDateTime(a.last_detected_at)}</Row>
            <Row label="Detections">{a.detection_count}</Row>
            <Row label="Guards">
              {a.recipient_count ?? a.recipients.length} received · {a.acknowledged_count ?? 0} acknowledged · {a.responding_count ?? 0} responding
            </Row>
            {closed && <Row label="Closed">{formatDateTime(a.closed_at)}</Row>}
            {closed && <Row label={a.status === "DISMISSED" ? "Reason" : "Note"}>{a.close_note ?? "—"}</Row>}
          </dl>
          <p className="muted small">The location is where the camera was when the alert was raised.</p>
        </section>
        <section className="card" aria-labelledby="history-title">
          <h2 id="history-title">Response history</h2>
          {history.error ? (
            <ErrorState error={history.error} onRetry={history.reload} />
          ) : history.loading && !history.data ? (
            <LoadingState />
          ) : (
            <ol className="plain timeline">
              {(history.data ?? []).map((e, i) => {
                const details = eventDetails(e);
                return (
                  <li key={`${e.created_at}-${i}`}>
                    <p>
                      <strong>{eventLabel(e)}</strong>
                      {e.to_status && e.from_status !== e.to_status && (
                        <>
                          {" "}
                          <StatusBadge status={e.to_status} />
                        </>
                      )}
                    </p>
                    <p className="muted small">
                      {e.actor_name ?? ROLE_LABELS[e.actor_role] ?? e.actor_role}
                      {e.actor_name && ` · ${ROLE_LABELS[e.actor_role] ?? e.actor_role}`} ·{" "}
                      <time dateTime={e.created_at}>{formatDateTime(e.created_at)}</time>
                      {details && ` · ${details}`}
                    </p>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      </div>
      {staff && (
        <div className="grid-2">
          <AlertMediaCard alert={a} />
          <div className="stack">
            <HandlerCard key={`${a.handled_by ?? ""}-${a.updated_at}`} alert={a} closed={closed} myId={session?.account_id} onChanged={refresh} />
            <LinkedCasesCard alert={a} />
          </div>
        </div>
      )}
      {staff && <EscalationsCard alertId={a.id} version={a.updated_at} />}
      <section className="card" aria-labelledby="recipients-title">
        <h2 id="recipients-title">Guards who received this alert</h2>
        <DataTable
          caption="Guards who received this alert"
          columns={RECIPIENT_COLUMNS}
          rows={a.recipients}
          rowKey={(r) => r.guard_id}
          empty="No Guard has received this alert."
        />
      </section>
      {staff && <CallControls target={{ scope: "ALERT", id: a.id }} closed={closed} />}
      <AlertChat alertId={a.id} closed={closed} />
      {escalating && (
        <EscalateDialog
          onClose={() => setEscalating(false)}
          onSubmit={async (body) => {
            const result = await alerts.escalate(a.id, body);
            setEscalating(false);
            toast("success", `Alert escalated: ${result.added_guards} Guard${result.added_guards === 1 ? "" : "s"} added.`);
            refresh();
          }}
        />
      )}
      {savingEvidence && (
        <SaveEvidenceDialog
          origin={{ alertId: a.id }}
          defaultTitle={`${a.camera_name} ${a.alert_type.replace(/_/g, " ").toLowerCase()} ${formatDateTime(a.detected_at)}`}
          onClose={() => setSavingEvidence(false)}
          onSaved={(item) => {
            setSavingEvidence(false);
            toast("success", `"${item.title}" saved to the evidence vault.`);
          }}
        />
      )}
      {dialog}
    </section>
  );
}
