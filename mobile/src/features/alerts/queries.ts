import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { alertsApi } from "../../api/endpoints";
import type { ActionResponse, AlertResponse, Assessment, RecipientState } from "../../api/types";
import { newId } from "../../lib/ids";
import { qk } from "../../lib/queryKeys";
import { useRealtime } from "../../realtime/RealtimeProvider";

/** The alerts sent to this Guard (location routing, escalations, assistance broadcasts). */
export function useAlertsQuery() {
  const { pollMs } = useRealtime();
  return useQuery({
    queryKey: qk.alerts,
    queryFn: () => alertsApi.list(),
    refetchInterval: pollMs,
    refetchIntervalInBackground: false,
  });
}

export function useAlertQuery(alertId: string) {
  const { pollMs } = useRealtime();
  return useQuery({ queryKey: qk.alert(alertId), queryFn: () => alertsApi.get(alertId), refetchInterval: pollMs });
}

export type GuardResponseAction = "ACKNOWLEDGE" | "RESPONDING" | Assessment;

const STATE_AFTER: Partial<Record<GuardResponseAction, RecipientState>> = {
  ACKNOWLEDGE: "ACKNOWLEDGED",
  RESPONDING: "RESPONDING",
};

/** Applies a confirmed action to the cached alert, so the alarm stops without waiting for the next refetch. */
export function applyConfirmedAction(alert: AlertResponse, action: GuardResponseAction, result: ActionResponse) {
  const [mine, ...others] = alert.recipients;
  if (!mine) return { ...alert, status: result.status };
  const state = STATE_AFTER[action] ?? mine.state;
  const keepResponding = mine.state === "RESPONDING" && state === "ACKNOWLEDGED";
  return {
    ...alert,
    status: result.status,
    recipients: [
      {
        ...mine,
        state: keepResponding ? mine.state : state,
        assessment: action === "UNDER_CONTROL" || action === "ADDITIONAL_GUARDS_REQUIRED" ? action : mine.assessment,
      },
      ...others,
    ],
  };
}

/** Acknowledge / I'm Responding / Under Control / Additional Guards Required, each with a fresh client_action_id
 *  (resending the same request is safe on the server). */
export function useGuardAction(alertId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (action: GuardResponseAction) => {
      const clientActionId = newId();
      if (action === "ACKNOWLEDGE") return alertsApi.acknowledge(alertId, clientActionId);
      if (action === "RESPONDING") return alertsApi.responding(alertId, clientActionId);
      return alertsApi.assessment(alertId, action, clientActionId);
    },
    onSuccess: (result, action) => {
      const update = (alert: AlertResponse) => (alert.id === alertId ? applyConfirmedAction(alert, action, result) : alert);
      queryClient.setQueryData<AlertResponse[]>(qk.alerts, (list) => list?.map(update));
      queryClient.setQueryData<AlertResponse>(qk.alert(alertId), (alert) => (alert ? update(alert) : alert));
      void queryClient.invalidateQueries({ queryKey: qk.alerts });
      void queryClient.invalidateQueries({ queryKey: qk.responders(alertId) });
      void queryClient.invalidateQueries({ queryKey: qk.alertHistory(alertId) });
    },
  });
}
