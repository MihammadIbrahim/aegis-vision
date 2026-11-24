/**
 * Real-time signals for the signed-in web app (decisions D-025, D-026). One WebSocket per tab. Pages subscribe to the
 * signals they care about and re-read their data over REST (signals never carry message text). While the socket is not
 * open, pages fall back to polling, and the topbar says so honestly.
 */
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { getToken } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { RealtimeSocket, realtimeUrl, type SocketFactory } from "./socket";
import type { RealtimeEvent, SocketStatus } from "./types";

type Listener = (event: RealtimeEvent) => void;

export interface RealtimeValue {
  status: SocketStatus;
  live: boolean;
  lastEventAt: string | null;
  subscribe: (listener: Listener) => () => void;
}

const RealtimeContext = createContext<RealtimeValue>({
  status: "idle",
  live: false,
  lastEventAt: null,
  subscribe: () => () => undefined,
});

export function RealtimeProvider({ children, factory }: { children: ReactNode; factory?: SocketFactory }) {
  const { session, signOut } = useAuth();
  const [status, setStatus] = useState<SocketStatus>("idle");
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const listeners = useRef(new Set<Listener>());
  const socketRef = useRef<RealtimeSocket | null>(null);
  const signedIn = session !== null;

  useEffect(() => {
    if (!signedIn) return;
    let readyCount = 0;
    const socket = new RealtimeSocket(
      realtimeUrl(),
      getToken,
      {
        onStatus: setStatus,
        onEvent: (event) => {
          setLastEventAt(event.at);
          for (const listener of Array.from(listeners.current)) listener(event);
        },
        onSessionEnded: (code) => signOut(code),
        // After a reconnection (not the first connection), everyone re-reads once: signals sent while the socket was
        // closed are lost.
        onReady: () => {
          readyCount += 1;
          if (readyCount === 1) return;
          const at = new Date().toISOString();
          for (const listener of Array.from(listeners.current)) listener({ type: "event", event: "notification", at, kind: "RESYNC" });
        },
      },
      factory,
    );
    socketRef.current = socket;
    socket.start();
    const online = () => socket.reconnectNow();
    window.addEventListener("online", online);
    return () => {
      window.removeEventListener("online", online);
      socket.stop();
      socketRef.current = null;
      setStatus("idle");
    };
  }, [signedIn, signOut, factory]);

  const value = useMemo<RealtimeValue>(
    () => ({
      status,
      live: status === "open",
      lastEventAt,
      subscribe: (listener) => {
        listeners.current.add(listener);
        return () => {
          listeners.current.delete(listener);
        };
      },
    }),
    [status, lastEventAt],
  );
  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}

export function useRealtime(): RealtimeValue {
  return useContext(RealtimeContext);
}

/** Calls ``handler`` for every signal (kept in a ref, so the subscription is made once). */
export function useRealtimeEvents(handler: (event: RealtimeEvent) => void): void {
  const { subscribe } = useRealtime();
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  });
  useEffect(() => subscribe((event) => ref.current(event)), [subscribe]);
}

export interface LiveRefreshOptions {
  /** Several signals in quick succession cause one reload (milliseconds). */
  debounceMs?: number;
  /** While the WebSocket is not open, reload this often instead (milliseconds); 0 = no polling. */
  fallbackMs?: number;
}

/** Re-reads data when a matching signal arrives, and polls while real-time updates are down. Also re-reads after a
 * reconnection (the RESYNC signal), since signals sent while the socket was closed are lost. */
export function useLiveRefresh(
  reload: () => void,
  matches: (event: RealtimeEvent) => boolean,
  { debounceMs = 300, fallbackMs = 0 }: LiveRefreshOptions = {},
): void {
  const { live } = useRealtime();
  const reloadRef = useRef(reload);
  const matchRef = useRef(matches);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    reloadRef.current = reload;
    matchRef.current = matches;
  });
  useRealtimeEvents((event) => {
    if (event.kind !== "RESYNC" && !matchRef.current(event)) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      reloadRef.current();
    }, debounceMs);
  });
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  useEffect(() => {
    if (live || fallbackMs <= 0) return;
    const poll = setInterval(() => reloadRef.current(), fallbackMs);
    return () => clearInterval(poll);
  }, [live, fallbackMs]);
}
