import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect, useMemo, useRef, useState } from "react";

import type { RealtimeServerMessage } from "../api/types";
import { useSession } from "../auth/SessionProvider";
import { POLL_FAST_MS, POLL_SLOW_MS, websocketUrl } from "../config";
import { useOnline } from "../lib/network";
import { qk } from "../lib/queryKeys";
import { RealtimeSocket, type SocketFactory, type SocketStatus } from "./socket";

type RealtimeEvent = Extract<RealtimeServerMessage, { type: "event" }>;
type Listener = (event: RealtimeEvent) => void;

interface RealtimeValue {
  status: SocketStatus;
  /** True while signals arrive over the WebSocket; polling then only runs as a slow safety net. */
  live: boolean;
  /** Polling interval for lists that must stay fresh (alerts, chat, calls). */
  pollMs: number;
  lastEventAt: string | null;
  subscribe: (listener: Listener) => () => void;
}

const RealtimeContext = createContext<RealtimeValue>({
  status: "idle",
  live: false,
  pollMs: POLL_FAST_MS,
  lastEventAt: null,
  subscribe: () => () => undefined,
});

/** What each signal makes stale. Signals carry IDs only; the content is fetched again over REST. */
export function invalidationsFor(event: RealtimeEvent): readonly (readonly unknown[])[] {
  const keys: (readonly unknown[])[] = [];
  switch (event.event) {
    case "alert.assigned":
    case "alert.updated":
      keys.push(qk.alerts);
      if (event.alert_id) keys.push(qk.alert(event.alert_id), qk.responders(event.alert_id));
      break;
    case "alert.message":
      if (event.alert_id) keys.push(qk.alertMessages(event.alert_id));
      break;
    case "incident.message":
      if (event.incident_id) keys.push(qk.incidentMessages(event.incident_id));
      break;
    case "incident.updated":
    case "incident.invited":
    case "incident.removed":
      keys.push(qk.incidents);
      break;
    case "notification":
      keys.push(qk.notifications);
      if (event.alert_id) keys.push(qk.alerts);
      break;
    case "call.invited":
    case "call.updated":
      keys.push(qk.myCalls);
      if (event.call_id) keys.push(qk.call(event.call_id));
      if (event.alert_id) keys.push(qk.activeCall("ALERT", event.alert_id));
      if (event.incident_id) keys.push(qk.activeCall("INCIDENT", event.incident_id));
      break;
    default:
      break;
  }
  return keys;
}

export function RealtimeProvider({ children, socketFactory }: { children: ReactNode; socketFactory?: SocketFactory }) {
  const { state, signOut, token } = useSession();
  const queryClient = useQueryClient();
  const online = useOnline();
  const [status, setStatus] = useState<SocketStatus>("idle");
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const listeners = useRef(new Set<Listener>());
  const socketRef = useRef<RealtimeSocket | null>(null);
  const signedIn = state.status === "signedIn";

  useEffect(() => {
    if (!signedIn) return;
    const socket = new RealtimeSocket(
      websocketUrl(),
      token,
      {
        onStatus: setStatus,
        onEvent: (event) => {
          setLastEventAt(event.at);
          for (const key of invalidationsFor(event)) void queryClient.invalidateQueries({ queryKey: key });
          for (const listener of Array.from(listeners.current)) listener(event);
        },
        onSessionEnded: (code, detail) => void signOut({ code, message: detail }),
        onReady: () => void queryClient.invalidateQueries({ queryKey: qk.alerts }),
      },
      socketFactory,
    );
    socketRef.current = socket;
    socket.start();
    return () => {
      socket.stop();
      socketRef.current = null;
      setStatus("idle");
    };
  }, [signedIn, token, signOut, queryClient, socketFactory]);

  useEffect(() => {
    if (online) socketRef.current?.reconnectNow();
  }, [online]);

  const value = useMemo<RealtimeValue>(() => {
    const live = status === "open";
    return {
      status,
      live,
      pollMs: live ? POLL_SLOW_MS : POLL_FAST_MS,
      lastEventAt,
      subscribe: (listener) => {
        listeners.current.add(listener);
        return () => {
          listeners.current.delete(listener);
        };
      },
    };
  }, [status, lastEventAt]);

  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}

export function useRealtime(): RealtimeValue {
  return useContext(RealtimeContext);
}
