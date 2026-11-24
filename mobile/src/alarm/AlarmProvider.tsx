/**
 * The persistent alert alarm (decision D-025).
 *
 * Rule: while an open alert sent to this Guard is not acknowledged (the Guard's recipient state is still NOTIFIED),
 * the siren loops and the phone vibrates, and a full-screen alarm covers the app. It stops only when the server has
 * confirmed "Acknowledge Alert" or "I'm Responding" for every such alert, when the alerts are resolved or dismissed,
 * or when the session ends. Tapping outside or the Android back button does not silence it; "View details" only
 * shrinks it to a red banner (the siren keeps ringing) so the Guard can read the alert first.
 */
import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AppState } from "react-native";

import { alertsApi } from "../api/endpoints";
import { ApiError, errorMessage } from "../api/client";
import type { AlertResponse } from "../api/types";
import { useSession } from "../auth/SessionProvider";
import { applyConfirmedAction, useAlertsQuery } from "../features/alerts/queries";
import { ALERT_TYPE_LABEL, byUrgency, isEscalatedToMe, needsAcknowledgement } from "../lib/format";
import { newId } from "../lib/ids";
import { qk } from "../lib/queryKeys";
import { notifyAlarmInBackground } from "../notifications/notifications";
import { type AlarmEngine, ExpoAlarmEngine } from "./engine";

export interface AlarmValue {
  /** Alerts the alarm rings for, most urgent first. */
  pending: AlertResponse[];
  ringing: boolean;
  minimized: boolean;
  busyAlertId: string | null;
  error: string | null;
  minimize: () => void;
  restore: () => void;
  confirm: (alert: AlertResponse, action: "ACKNOWLEDGE" | "RESPONDING") => Promise<boolean>;
  /** Plays the siren for a few seconds (Profile & Settings: check the speaker before a shift). */
  test: (seconds?: number) => Promise<void>;
  testing: boolean;
}

const AlarmContext = createContext<AlarmValue | null>(null);

export function AlarmProvider({ children, engine: injected }: { children: ReactNode; engine?: AlarmEngine }) {
  const [engine] = useState<AlarmEngine>(() => injected ?? new ExpoAlarmEngine());
  const queryClient = useQueryClient();
  const { onSignOut } = useSession();
  const alerts = useAlertsQuery();
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const [minimizedFor, setMinimizedFor] = useState<string | null>(null);
  const [busyAlertId, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const notified = useRef(new Set<string>());

  const pending = useMemo(
    () => (alerts.data ?? []).filter((a) => needsAcknowledgement(a) && !confirmed.has(a.id)).sort(byUrgency),
    [alerts.data, confirmed],
  );
  const ringing = pending.length > 0;
  const signature = pending.map((a) => a.id).join(",");
  // A new unacknowledged alert brings the full-screen alarm back even if an earlier one was minimized.
  const minimized = ringing && minimizedFor === signature;

  useEffect(() => {
    if (ringing) void engine.start();
    else if (!testing) void engine.stop();
  }, [ringing, testing, engine]);

  // A confirmation is final on the server (a recipient never goes back to NOTIFIED, decision D-013), so the
  // confirmed IDs only bridge the moment until the refreshed alert list says the same.

  // In the background, also post a loud local notification on the alarm channel (once per alert).
  useEffect(() => {
    if (AppState.currentState === "active") return;
    for (const alert of pending) {
      if (notified.current.has(alert.id)) continue;
      notified.current.add(alert.id);
      void notifyAlarmInBackground(
        alert.id,
        `${alert.priority === "HIGH" ? "HIGH PRIORITY: " : ""}${ALERT_TYPE_LABEL[alert.alert_type]}`,
        "Open the Guard app to acknowledge this alert.",
      ).catch(() => undefined);
    }
  }, [pending]);

  // Session ended (sign-out, ASSIGNMENT_CHANGED, ORGANIZATION_INACTIVE): silence everything.
  useEffect(
    () =>
      onSignOut(async () => {
        setConfirmed(new Set());
        await engine.stop();
      }),
    [onSignOut, engine],
  );
  useEffect(() => () => void engine.stop(), [engine]);

  const confirm = useCallback(
    async (alert: AlertResponse, action: "ACKNOWLEDGE" | "RESPONDING") => {
      setBusy(alert.id);
      setError(null);
      try {
        const clientActionId = newId();
        const result =
          action === "ACKNOWLEDGE"
            ? await alertsApi.acknowledge(alert.id, clientActionId)
            : await alertsApi.responding(alert.id, clientActionId);
        // Confirmed by the server (DONE, UNCHANGED or REPLAYED): only now may this alert stop ringing.
        setConfirmed((set) => new Set(set).add(alert.id));
        queryClient.setQueryData<AlertResponse[]>(qk.alerts, (list) =>
          list?.map((a) => (a.id === alert.id ? applyConfirmedAction(a, action, result) : a)),
        );
        void queryClient.invalidateQueries({ queryKey: qk.alerts });
        void queryClient.invalidateQueries({ queryKey: qk.alert(alert.id) });
        return true;
      } catch (failure) {
        if (failure instanceof ApiError && failure.code === "ALERT_CLOSED") {
          void queryClient.invalidateQueries({ queryKey: qk.alerts }); // closed meanwhile: stops on refetch
          return true;
        }
        setError(
          failure instanceof ApiError && failure.isNetworkError
            ? "Not confirmed: no connection to the server. The alarm keeps ringing until the server confirms."
            : `Not confirmed: ${errorMessage(failure)}`,
        );
        return false;
      } finally {
        setBusy(null);
      }
    },
    [queryClient],
  );

  const test = useCallback(
    async (seconds = 3) => {
      if (ringing) return;
      setTesting(true);
      await engine.start();
      await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
      await engine.stop();
      setTesting(false);
    },
    [engine, ringing],
  );

  const value = useMemo<AlarmValue>(
    () => ({
      pending,
      ringing,
      minimized,
      busyAlertId,
      error,
      minimize: () => setMinimizedFor(signature),
      restore: () => setMinimizedFor(null),
      confirm,
      test,
      testing,
    }),
    [pending, ringing, minimized, busyAlertId, error, signature, confirm, test, testing],
  );

  return <AlarmContext.Provider value={value}>{children}</AlarmContext.Provider>;
}

export function useAlarm(): AlarmValue {
  const value = useContext(AlarmContext);
  if (!value) throw new Error("useAlarm must be used inside AlarmProvider");
  return value;
}

/** "I'm Responding" is the main action for alerts that reached this Guard through an escalation. */
export function primaryActionFor(alert: AlertResponse): "ACKNOWLEDGE" | "RESPONDING" {
  return isEscalatedToMe(alert) ? "RESPONDING" : "ACKNOWLEDGE";
}
