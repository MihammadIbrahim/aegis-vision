import { api } from "../../api/client";
import type { AppNotification } from "./types";

/** Fired after notifications are read here, so the topbar count refreshes at once. */
export const NOTIFICATIONS_CHANGED = "cctv:notifications-changed";

function changed(): void {
  window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED));
}

export const notifications = {
  list: (unreadOnly: boolean, limit = 100) =>
    api<AppNotification[]>("/notifications", { query: { unread_only: unreadOnly, limit } }),
  read: async (id: string) => {
    await api<void>(`/notifications/${id}/read`, { method: "POST" });
    changed();
  },
  readAll: async () => {
    const result = await api<{ marked_read: number }>("/notifications/read-all", { method: "POST" });
    changed();
    return result;
  },
};
