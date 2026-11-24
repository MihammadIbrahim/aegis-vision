import { Bell } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { notifications, NOTIFICATIONS_CHANGED } from "../api";

const LIMIT = 100;
const REFRESH_MS = 60_000;

/** Topbar link to Notifications with the unread count. The count is read again at once when a `notification`
 * signal arrives over the WebSocket (decision D-026), when the page changes, after notifications are read, and once
 * a minute as the polling fallback. */
export function NotificationBell() {
  const [unread, setUnread] = useState<number | null>(null);
  const location = useLocation();

  const load = useCallback(() => {
    notifications
      .list(true, LIMIT)
      .then((items) => setUnread(items.length))
      .catch(() => setUnread(null));
  }, []);

  useEffect(load, [load, location.pathname]);
  useLiveRefresh(load, (e) => e.event === "notification", { debounceMs: 100 });
  useEffect(() => {
    const timer = window.setInterval(load, REFRESH_MS);
    window.addEventListener(NOTIFICATIONS_CHANGED, load);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(NOTIFICATIONS_CHANGED, load);
    };
  }, [load]);

  const count = unread ?? 0;
  const text = count >= LIMIT ? `${LIMIT - 1}+` : String(count);
  const label = count > 0 ? `Notifications, ${text} unread` : "Notifications";
  return (
    <Link className="btn btn-ghost btn-sm topbar-icon bell" to="/notifications" aria-label={label} title={label}>
      <Bell className="icon" aria-hidden="true" />
      {count > 0 && (
        <span className="bell-count" aria-hidden="true">
          {text}
        </span>
      )}
    </Link>
  );
}
