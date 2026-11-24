import { RotateCw, Send } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button, ErrorState, Field, LoadingState, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { alerts } from "../api";
import { ROLE_LABELS } from "../labels";

/** The alert's chat between the campus's staff and the alert's Guards (15.5). New messages arrive live: an
 * `alert.message` signal makes the chat read the thread again (the signal never carries the text; decision D-026).
 * Without the WebSocket the thread is read every 10 s. */
export function AlertChat({ alertId, closed }: { alertId: string; closed: boolean }) {
  const toast = useToast();
  const thread = useAsync(() => alerts.messages(alertId), [alertId]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [posted, setPosted] = useState<Awaited<ReturnType<typeof alerts.messages>> | null>(null);
  const messages = posted ?? thread.data ?? [];

  async function send(event: FormEvent) {
    event.preventDefault();
    const body = text.trim();
    if (!body) return;
    setBusy(true);
    try {
      setPosted(await alerts.post(alertId, body));
      setText("");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function refresh() {
    setPosted(null);
    thread.reload();
  }
  useLiveRefresh(refresh, (e) => e.event === "alert.message" && e.alert_id === alertId, { debounceMs: 150, fallbackMs: 10_000 });

  return (
    <section className="card" aria-labelledby="chat-title">
      <div className="card-head">
        <h2 id="chat-title">Alert chat</h2>
        <Button variant="ghost" size="sm" icon={RotateCw} onClick={refresh}>
          Refresh
        </Button>
      </div>
      {thread.error ? (
        <ErrorState error={thread.error} onRetry={refresh} />
      ) : thread.loading && !thread.data ? (
        <LoadingState />
      ) : messages.length === 0 ? (
        <p className="muted">No messages yet.</p>
      ) : (
        <ol className="plain chat" aria-label="Messages">
          {messages.map((m) => (
            <li key={m.id} className={`chat-message${m.sender_role === "GUARD" ? "" : " is-staff"}`}>
              <p className="chat-meta">
                <strong>{m.sender_name}</strong> <span className="muted">· {ROLE_LABELS[m.sender_role] ?? m.sender_role}</span>{" "}
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
        <p className="muted small">This alert is closed, so no new messages can be posted.</p>
      ) : (
        <form className="stack" onSubmit={send}>
          <Field label="Message" required hint="Seen by this campus's Admins and Supervisors and the alert's Guards.">
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
