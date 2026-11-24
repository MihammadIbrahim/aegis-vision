import { CheckCheck, ExternalLink, MailOpen, RotateCw } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button, EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { formatDateTime } from "../../../lib/format";
import { notifications } from "../api";
import { KIND_LABELS, KIND_OPTIONS, notificationTarget as target } from "../labels";
import type { AppNotification } from "../types";

const LIMIT = 100;

/** The signed-in user's own notifications for this campus, newest first. */
export function NotificationsPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [kind, setKind] = useState("");
  const list = useAsync(() => notifications.list(unreadOnly, LIMIT), [unreadOnly]);
  useLiveRefresh(list.reload, (e) => e.event === "notification");
  const [busy, setBusy] = useState<string | null>(null);
  const items = (list.data ?? []).filter((n) => !kind || n.kind === kind);
  const anyUnread = (list.data ?? []).some((n) => !n.read_at);

  async function markRead(n: AppNotification) {
    setBusy(n.id);
    try {
      await notifications.read(n.id);
      list.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function open(n: AppNotification) {
    const to = target(n);
    if (!to) return;
    if (!n.read_at) {
      try {
        await notifications.read(n.id);
      } catch {
        // Opening the record matters more than the read mark.
      }
    }
    navigate(to.to);
  }

  async function markAll() {
    setBusy("all");
    try {
      const { marked_read } = await notifications.readAll();
      toast("success", marked_read === 1 ? "1 notification marked as read." : `${marked_read} notifications marked as read.`);
      list.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <PageHeader
        title="Notifications"
        description="In-app notifications about this campus's alerts and manual reports. They are shown here only; nothing is sent to phones or e-mail."
        actions={
          <>
            <Button variant="ghost" icon={RotateCw} onClick={list.reload}>
              Refresh
            </Button>
            <Button variant="secondary" icon={CheckCheck} loading={busy === "all"} disabled={!anyUnread} onClick={() => void markAll()}>
              Mark all as read
            </Button>
          </>
        }
      />
      <div className="filters" role="group" aria-label="Filter notifications">
        <label className="checkbox">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
          Unread only
        </label>
        <select aria-label="Type" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All types</option>
          {KIND_OPTIONS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </div>
      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : list.loading && !list.data ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState title={unreadOnly ? "No unread notifications." : "No notifications."} />
      ) : (
        <>
          <ul className="plain notification-list" aria-label="Notifications">
            {items.map((n) => {
              const to = target(n);
              return (
                <li key={n.id} className={`card notification${n.read_at ? "" : " is-unread"}`}>
                  <div className="notification-main">
                    <p className="notification-title">
                      <StatusBadge status={n.read_at ? "READ" : "UNREAD"} /> {n.title}
                    </p>
                    <p className="muted small">
                      {KIND_LABELS[n.kind] ?? n.kind} · <time dateTime={n.created_at}>{formatDateTime(n.created_at)}</time>
                    </p>
                  </div>
                  <div className="actions">
                    {to && (
                      <Button size="sm" variant="secondary" icon={ExternalLink} onClick={() => void open(n)}>
                        {to.label}
                      </Button>
                    )}
                    {!n.read_at && (
                      <Button size="sm" variant="ghost" icon={MailOpen} loading={busy === n.id} onClick={() => void markRead(n)}>
                        Mark as read
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {(list.data ?? []).length >= LIMIT && <p className="muted small">Showing the latest {LIMIT} notifications.</p>}
        </>
      )}
    </section>
  );
}
