import { FolderPlus, UserCheck, Video } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { AuthMedia } from "../../../components/AuthMedia";
import { Button, ErrorState, LoadingState, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { incidents } from "../../incidents/api";
import { IncidentForm } from "../../incidents/components/IncidentForm";
import { alerts } from "../api";
import { ROLE_LABELS, TYPE_OPTIONS } from "../labels";
import type { Alert } from "../types";

const TYPE_TITLES: Record<string, string> = Object.fromEntries(TYPE_OPTIONS);

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The detection snapshot inline, and the recording around the detection on request (Org Admin page 4). */
export function AlertMediaCard({ alert }: { alert: Alert }) {
  const [showClip, setShowClip] = useState(false);
  return (
    <section className="card" aria-labelledby="alert-media-title">
      <h2 id="alert-media-title">What the camera saw</h2>
      <AuthMedia
        path={alerts.paths.snapshot(alert.id)}
        alt={`Detection snapshot from ${alert.camera_name}`}
        className="media-frame"
        fallback="No snapshot or recording is kept for this detection."
      />
      <p className="muted small">
        {alert.has_snapshot ? "The frame the detector sent with the alert." : "No detector snapshot was kept; this is the recorded frame at the detection time."}
      </p>
      {showClip ? (
        <AuthMedia
          path={alerts.paths.clip(alert.id)}
          alt={`Recording around the detection on ${alert.camera_name}`}
          kind="video"
          className="media-frame"
          fallback="No recording is kept for this moment."
        />
      ) : (
        <div className="actions">
          <Button variant="secondary" icon={Video} onClick={() => setShowClip(true)}>
            Play the recording around the detection
          </Button>
        </div>
      )}
    </section>
  );
}

/** Who is responsible for the alert: any Admin or Supervisor of the campus, while the alert is open. */
export function HandlerCard({ alert, closed, myId, onChanged }: { alert: Alert; closed: boolean; myId?: string; onChanged: () => void }) {
  const toast = useToast();
  const staff = useAsync(() => (closed ? Promise.resolve([]) : incidents.staff()), [closed]);
  const [choice, setChoice] = useState(alert.handled_by ?? "");
  const [busy, setBusy] = useState(false);
  async function save(userId: string | null) {
    setBusy(true);
    try {
      await alerts.setHandler(alert.id, userId);
      toast("success", userId ? "Handler set." : "Handler cleared.");
      onChanged();
    } catch (e) {
      toast("error", message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card" aria-labelledby="handler-title">
      <h2 id="handler-title">Handled by</h2>
      <p>
        {alert.handled_by_name ? (
          <>
            <strong>{alert.handled_by_name}</strong>
            {alert.handled_at && <span className="muted small"> · since {formatDateTime(alert.handled_at)}</span>}
          </>
        ) : (
          <span className="muted">Nobody has taken this alert yet.</span>
        )}
      </p>
      {!closed && (
        <div className="actions">
          {myId && alert.handled_by !== myId && (
            <Button size="sm" icon={UserCheck} loading={busy} onClick={() => void save(myId)}>
              I'll handle it
            </Button>
          )}
          <select aria-label="Handler" value={choice} onChange={(e) => setChoice(e.target.value)} disabled={busy || !staff.data}>
            <option value="">Nobody</option>
            {(staff.data ?? []).map((s) => (
              <option key={s.user_id} value={s.user_id}>
                {s.full_name} ({ROLE_LABELS[s.role] ?? s.role})
              </option>
            ))}
          </select>
          <Button size="sm" variant="secondary" disabled={busy || choice === (alert.handled_by ?? "")} onClick={() => void save(choice || null)}>
            Save handler
          </Button>
        </div>
      )}
      {staff.error ? <ErrorState error={staff.error} onRetry={staff.reload} /> : null}
    </section>
  );
}

const SCOPE_TEXT: Record<string, string> = {
  CAMPUS: "To every Guard of the campus",
  LOCATION: "To the Guards of a location",
  GUARDS: "To chosen Guards",
  AUTOMATIC: "Automatic: no Guard acknowledged in time",
  ASSISTANCE: "Assistance broadcast: a Guard asked for more Guards",
};

/** Every escalation of the alert, manual and automatic, with the Guards each one added. */
export function EscalationsCard({ alertId, version }: { alertId: string; version: string }) {
  const list = useAsync(() => alerts.escalations(alertId), [alertId, version]);
  return (
    <section className="card" aria-labelledby="escalations-title">
      <h2 id="escalations-title">Escalations</h2>
      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingState />
      ) : list.data.length === 0 ? (
        <p className="muted small">The alert has not been escalated.</p>
      ) : (
        <ol className="plain timeline">
          {list.data.map((e) => (
            <li key={e.id}>
              <p>
                <strong>{SCOPE_TEXT[e.scope] ?? e.scope}</strong> · {e.added_count} Guard{e.added_count === 1 ? "" : "s"} added
              </p>
              <p className="muted small">
                {e.escalated_by_name ? `${e.escalated_by_name} · ${ROLE_LABELS[e.escalated_by_role ?? ""] ?? e.escalated_by_role ?? ""}` : "System"} ·{" "}
                <time dateTime={e.created_at}>{formatDateTime(e.created_at)}</time>
              </p>
              {e.note && <p className="small prewrap">{e.note}</p>}
              {e.guards.length > 0 && <p className="small">{e.guards.join(", ")}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** The cases (incidents) this alert belongs to, and a way to open a new case from it. */
export function LinkedCasesCard({ alert }: { alert: Alert }) {
  const toast = useToast();
  const cases = useAsync(() => incidents.forAlert(alert.id), [alert.id]);
  const [opening, setOpening] = useState(false);
  return (
    <section className="card" aria-labelledby="cases-title">
      <div className="card-head">
        <h2 id="cases-title">Cases</h2>
        <Button size="sm" variant="secondary" icon={FolderPlus} onClick={() => setOpening(true)}>
          Open a case
        </Button>
      </div>
      {cases.error ? (
        <ErrorState error={cases.error} onRetry={cases.reload} />
      ) : !cases.data ? (
        <LoadingState />
      ) : cases.data.length === 0 ? (
        <p className="muted small">This alert is not part of any case.</p>
      ) : (
        <ul className="plain stack">
          {cases.data.map((c) => (
            <li key={c.id} className="row">
              <Link to={`/incidents/${c.id}`}>
                <span className="mono">{c.reference}</span> · {c.title}
              </Link>
              <StatusBadge status={c.status} />
            </li>
          ))}
        </ul>
      )}
      {opening && (
        <IncidentForm
          alertId={alert.id}
          initial={{
            title: `${TYPE_TITLES[alert.alert_type] ?? "Alert"} · ${alert.camera_name}`,
            priority: alert.priority,
            occurred_at: alert.detected_at,
            camera_id: alert.camera_id,
          }}
          onClose={() => setOpening(false)}
          onSaved={(created) => {
            setOpening(false);
            toast("success", `Case ${created.reference} opened.`);
            cases.reload();
          }}
        />
      )}
    </section>
  );
}
