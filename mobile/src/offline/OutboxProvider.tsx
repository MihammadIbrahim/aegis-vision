import AsyncStorage from "@react-native-async-storage/async-storage";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { guardApi, reportsApi } from "../api/endpoints";
import type { ManualReportRequest } from "../api/types";
import { useSession } from "../auth/SessionProvider";
import { useOnline } from "../lib/network";
import { qk } from "../lib/queryKeys";
import { STORAGE_KEYS } from "../lib/storageKeys";
import { deviceOutboxStore, flushOutbox, type OutboxItem, type OutboxStore } from "./outbox";

export type SubmitOutcome = "SENT" | "QUEUED";

interface OutboxValue {
  /** The signed-in Guard's queued reports. */
  items: OutboxItem[];
  flushing: boolean;
  lastSyncAt: string | null;
  lastError: string | null;
  /** Sends now when online; queues when offline or when the network fails. Validation errors are thrown. */
  submit: (report: ManualReportRequest, display: OutboxItem["display"]) => Promise<SubmitOutcome>;
  retryAll: () => Promise<void>;
  discard: (clientReportId: string) => Promise<void>;
}

const OutboxContext = createContext<OutboxValue | null>(null);

const AUTO_RETRY_MS = 60_000;

export function OutboxProvider({ children, store = deviceOutboxStore }: { children: ReactNode; store?: OutboxStore }) {
  const { state } = useSession();
  const online = useOnline();
  const queryClient = useQueryClient();
  const [all, setAll] = useState<OutboxItem[]>([]);
  const [flushing, setFlushing] = useState(false);
  const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const allRef = useRef<OutboxItem[]>([]);
  const flushingRef = useRef(false);
  const signedIn = state.status === "signedIn";

  // The Guard ID tags queued reports, so they are only ever sent for the Guard who wrote them.
  const dashboard = useQuery({ queryKey: qk.dashboard, queryFn: guardApi.dashboard, enabled: signedIn });
  const owner = signedIn ? (dashboard.data?.guard.guard_id ?? null) : null;

  const persist = useCallback(
    async (items: OutboxItem[]) => {
      allRef.current = items;
      setAll(items);
      await store.save(items);
    },
    [store],
  );

  useEffect(() => {
    void store.load().then((items) => {
      allRef.current = items;
      setAll(items);
    });
    void AsyncStorage.getItem(STORAGE_KEYS.lastSync).then(setLastSyncAt);
  }, [store]);

  const flush = useCallback(
    async (includeFailed: boolean) => {
      if (!owner || flushingRef.current) return;
      if (!allRef.current.some((i) => i.owner_guard_id === owner && (i.state === "PENDING" || includeFailed))) return;
      flushingRef.current = true;
      setFlushing(true);
      setLastError(null);
      await persist(
        allRef.current.map((i) =>
          i.owner_guard_id === owner && (i.state === "PENDING" || (includeFailed && i.state === "FAILED"))
            ? { ...i, state: "RETRYING" }
            : i,
        ),
      );
      try {
        const asPending = allRef.current.map((i) => (i.state === "RETRYING" ? { ...i, state: "PENDING" as const } : i));
        const result = await flushOutbox(asPending, owner, store, reportsApi.sync, includeFailed);
        await persist(result.items);
        if (result.synced > 0) {
          const now = new Date().toISOString();
          setLastSyncAt(now);
          await AsyncStorage.setItem(STORAGE_KEYS.lastSync, now);
          void queryClient.invalidateQueries({ queryKey: qk.reports });
        }
        const stillPending = result.items.some((i) => i.owner_guard_id === owner && i.state === "PENDING");
        if (stillPending) setLastError("Some reports are still waiting for a connection.");
      } finally {
        flushingRef.current = false;
        setFlushing(false);
      }
    },
    [owner, persist, store, queryClient],
  );

  // Flush on reconnect, at sign-in, and every minute while something is waiting.
  useEffect(() => {
    if (online && owner) void flush(false);
  }, [online, owner, flush]);
  useEffect(() => {
    if (!online || !owner) return;
    const timer = setInterval(() => void flush(false), AUTO_RETRY_MS);
    return () => clearInterval(timer);
  }, [online, owner, flush]);

  const submit = useCallback(
    async (report: ManualReportRequest, display: OutboxItem["display"]): Promise<SubmitOutcome> => {
      if (!owner) throw new Error("Your account details are still loading. Try again in a moment.");
      if (online) {
        try {
          await reportsApi.submit(report);
          void queryClient.invalidateQueries({ queryKey: qk.reports });
          return "SENT";
        } catch (error) {
          if (!(error instanceof ApiError) || !(error.isNetworkError || error.status >= 500)) throw error;
        }
      }
      const { photo_base64, ...rest } = report;
      const photo = photo_base64 ? await store.writePhoto(report.client_report_id, photo_base64) : null;
      const item: OutboxItem = {
        client_report_id: report.client_report_id,
        owner_guard_id: owner,
        report: rest,
        photo_file: photo,
        state: "PENDING",
        attempts: 0,
        last_error: null,
        queued_at: new Date().toISOString(),
        display,
      };
      await persist([...allRef.current.filter((i) => i.client_report_id !== item.client_report_id), item]);
      return "QUEUED";
    },
    [owner, online, persist, store, queryClient],
  );

  const discard = useCallback(
    async (clientReportId: string) => {
      const item = allRef.current.find((i) => i.client_report_id === clientReportId);
      if (item?.photo_file) await store.deletePhoto(item.photo_file).catch(() => undefined);
      await persist(allRef.current.filter((i) => i.client_report_id !== clientReportId));
    },
    [persist, store],
  );

  const value = useMemo<OutboxValue>(
    () => ({
      items: owner ? all.filter((i) => i.owner_guard_id === owner) : [],
      flushing,
      lastSyncAt,
      lastError,
      submit,
      retryAll: () => flush(true),
      discard,
    }),
    [all, owner, flushing, lastSyncAt, lastError, submit, flush, discard],
  );

  return <OutboxContext.Provider value={value}>{children}</OutboxContext.Provider>;
}

export function useOutbox(): OutboxValue {
  const value = useContext(OutboxContext);
  if (!value) throw new Error("useOutbox must be used inside OutboxProvider");
  return value;
}
