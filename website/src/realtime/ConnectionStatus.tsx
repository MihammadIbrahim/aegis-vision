import { Radio, RefreshCw, WifiOff } from "lucide-react";

import { useRealtime } from "./RealtimeProvider";

/** The honest state of live updates: "Live" only while the WebSocket is open and signed in. */
export function ConnectionStatus() {
  const { status } = useRealtime();
  const look =
    status === "open"
      ? { cls: "is-live", Icon: Radio, label: "Live", title: "Live updates are on (WebSocket connected)." }
      : status === "connecting" || status === "reconnecting"
        ? {
            cls: "is-reconnecting",
            Icon: RefreshCw,
            label: status === "connecting" ? "Connecting…" : "Reconnecting…",
            title: "Live updates are reconnecting; pages refresh by polling meanwhile.",
          }
        : { cls: "is-polling", Icon: WifiOff, label: "Polling", title: "Live updates are off; pages refresh by polling." };
  return (
    <span className={`live-status ${look.cls}`} role="status" aria-live="polite" title={look.title} data-testid="live-status">
      <look.Icon className="icon" aria-hidden="true" />
      <span className="live-status-label">{look.label}</span>
    </span>
  );
}
