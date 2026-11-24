/**
 * Screen 6 — Alert Details & Actions. Metadata, the Guard's own response, Acknowledge / Under Control / Additional
 * Guards Required / I'm Responding (each with a client_action_id), the responder roster, the history, the alert chat
 * and an in-app call. Snapshots and clips are staff-only in the Core API (decision D-023): the screen says so.
 */
import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { errorMessage } from "../../api/client";
import { alertsApi } from "../../api/endpoints";
import type { AlertResponse } from "../../api/types";
import { ActiveCallNotice, CallButton } from "../../calls/CallViews";
import { Badge, PriorityBadge, StatusBadge } from "../../components/Badge";
import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Header } from "../../components/Header";
import { Empty, ErrorState, Loading } from "../../components/States";
import {
  ALERT_TYPE_LABEL,
  REASON_LABEL,
  alertLocationText,
  confidenceText,
  formatDateTime,
  isClosed,
  isEscalatedToMe,
  myRecipient,
  timeAgo,
} from "../../lib/format";
import { qk } from "../../lib/queryKeys";
import { useRealtime } from "../../realtime/RealtimeProvider";
import { colors, radius } from "../../theme";
import { type GuardResponseAction, useAlertQuery, useGuardAction } from "./queries";

const EVENT_LABEL: Record<string, string> = {
  CREATED: "Alert created",
  DETECTION_REPEATED: "Detected again",
  ROUTED: "Sent to Guards",
  ACKNOWLEDGED: "Acknowledged",
  RESPONDING: "Responding",
  ASSESSMENT: "Assessment",
  ESCALATED: "Escalated",
  AUTO_ESCALATED: "Escalated automatically",
  ASSISTANCE_BROADCAST: "Assistance broadcast",
  HANDLER_ASSIGNED: "Handler assigned",
  RESOLVED: "Resolved",
  DISMISSED: "Dismissed",
};

export default function AlertDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const alertId = String(id);
  const alert = useAlertQuery(alertId);
  return (
    <View style={styles.flex}>
      <Header
        title={alert.data ? `Alert #${alert.data.id.slice(0, 8).toUpperCase()}` : "Alert details"}
        subtitle={alert.data ? ALERT_TYPE_LABEL[alert.data.alert_type] : undefined}
        back
      />
      {alert.isPending ? (
        <Loading />
      ) : alert.isError && !alert.data ? (
        <ErrorState error={alert.error} onRetry={() => void alert.refetch()} />
      ) : alert.data ? (
        <AlertDetail alert={alert.data} />
      ) : null}
    </View>
  );
}

export function AlertDetail({ alert }: { alert: AlertResponse }) {
  const router = useRouter();
  const { pollMs } = useRealtime();
  const action = useGuardAction(alert.id);
  const responders = useQuery({
    queryKey: qk.responders(alert.id),
    queryFn: () => alertsApi.responders(alert.id),
    refetchInterval: pollMs,
  });
  const history = useQuery({ queryKey: qk.alertHistory(alert.id), queryFn: () => alertsApi.history(alert.id) });
  const mine = myRecipient(alert);
  const closed = isClosed(alert.status);
  const acknowledged = mine !== null && mine.state !== "NOTIFIED";
  const responding = mine?.state === "RESPONDING";
  const escalated = isEscalatedToMe(alert);
  const confidence = confidenceText(alert.confidence);
  const run = (kind: GuardResponseAction) => action.mutate(kind);
  const pendingKind = action.isPending ? action.variables : null;

  return (
    <ScrollView contentContainerStyle={styles.body}>
      <Card testID="snapshot-panel">
        <View style={styles.snapshot}>
          <Ionicons name="image-outline" size={28} color={colors.textSubtle} />
          <Text style={styles.snapshotText}>
            {alert.has_snapshot
              ? "A detection snapshot is kept for this alert. Snapshots, clips and recordings are shown to Admins and Supervisors only; Guards have no access to video."
              : "No snapshot is available in the Guard app. Snapshots, clips and recordings are shown to Admins and Supervisors only."}
          </Text>
        </View>
      </Card>
      <Card>
        <View style={styles.badges}>
          <PriorityBadge priority={alert.priority} />
          <StatusBadge status={alert.status} />
          {mine && mine.reason !== "LOCATION" ? <Badge label={REASON_LABEL[mine.reason]} tone="info" /> : null}
        </View>
        <Text style={styles.type}>{ALERT_TYPE_LABEL[alert.alert_type]}</Text>
        <Field label="Camera" value={`${alert.camera_name} (ID ${alert.camera_id.slice(0, 8)}…)`} />
        <Field label="Location" value={alertLocationText(alert.location)} />
        <Field label="Detected" value={`${formatDateTime(alert.detected_at)} · ${timeAgo(alert.detected_at)}`} />
        {alert.detection_count > 1 ? (
          <Field label="Last detection" value={`${formatDateTime(alert.last_detected_at)} (${alert.detection_count} detections)`} />
        ) : null}
        {confidence ? <Field label="AI confidence" value={confidence} /> : null}
        {alert.handled_by_name ? <Field label="Handled by" value={alert.handled_by_name} /> : null}
        {closed && alert.close_note ? <Field label="Closing note" value={alert.close_note} /> : null}
        <Text style={styles.note}>An AI detection is a signal for you to check, not proof of what happened.</Text>
      </Card>

      {closed ? (
        <Banner tone="info" message="This alert is closed. You can still read its chat and history." />
      ) : (
        <Card testID="guard-actions">
          <Text style={styles.section}>Your response</Text>
          {action.isError ? <Banner tone="danger" message={errorMessage(action.error)} testID="action-error" /> : null}
          {!acknowledged ? (
            <Button
              testID="action-acknowledge"
              variant="outline"
              icon="checkmark-circle-outline"
              label="Acknowledge Alert"
              loading={pendingKind === "ACKNOWLEDGE"}
              disabled={action.isPending}
              onPress={() => run("ACKNOWLEDGE")}
            />
          ) : (
            <Badge label={responding ? "You are responding" : "You acknowledged this alert"} tone="success" />
          )}
          <View style={styles.row}>
            <Button
              testID="action-under-control"
              style={styles.half}
              variant="success"
              icon="shield-checkmark-outline"
              label="Under Control"
              loading={pendingKind === "UNDER_CONTROL"}
              disabled={!acknowledged || action.isPending}
              onPress={() => run("UNDER_CONTROL")}
            />
            <Button
              testID="action-additional"
              style={styles.half}
              variant="dangerSoft"
              icon="warning-outline"
              label="Additional Guards Required"
              loading={pendingKind === "ADDITIONAL_GUARDS_REQUIRED"}
              disabled={!acknowledged || action.isPending}
              onPress={() => run("ADDITIONAL_GUARDS_REQUIRED")}
            />
          </View>
          {!acknowledged ? <Text style={styles.hint}>Acknowledge first, then report your assessment.</Text> : null}
          {mine?.assessment ? (
            <Text style={styles.hint}>
              Your assessment: {mine.assessment === "UNDER_CONTROL" ? "Under Control" : "Additional Guards Required"}
              {mine.assessment === "ADDITIONAL_GUARDS_REQUIRED"
                ? " · the Admins and Supervisors were told, and more Guards are alerted if your organization allows it."
                : ""}
            </Text>
          ) : null}
          {!responding ? (
            <Button
              testID="action-responding"
              icon="navigate"
              label="I'm Responding"
              large={escalated}
              loading={pendingKind === "RESPONDING"}
              disabled={action.isPending}
              onPress={() => run("RESPONDING")}
            />
          ) : null}
        </Card>
      )}

      <Card testID="responders">
        <Text style={styles.section}>Responding Guards ({responders.data?.length ?? 0})</Text>
        {responders.data && responders.data.length > 0 ? (
          responders.data.map((r, index) => (
            <View key={`${r.full_name}-${index}`} style={styles.responder}>
              <Ionicons name="shield-half" size={22} color={colors.primary} />
              <View style={{ flex: 1 }}>
                <Text style={styles.responderName}>
                  {r.full_name}
                  {r.is_me ? " (you)" : ""}
                </Text>
                <Text style={styles.hint}>
                  {r.state === "RESPONDING" ? `En route since ${formatDateTime(r.responding_at)}` : "Acknowledged"}
                  {r.assessment ? ` · ${r.assessment === "UNDER_CONTROL" ? "Under Control" : "Needs more Guards"}` : ""}
                </Text>
              </View>
            </View>
          ))
        ) : (
          <Text style={styles.hint}>No Guard has acknowledged or confirmed responding yet.</Text>
        )}
      </Card>

      <ActiveCallNotice target={{ scope: "ALERT", id: alert.id }} />
      <View style={styles.row}>
        <Button
          testID="open-chat"
          style={styles.half}
          icon="chatbubbles"
          label="Group Chat"
          onPress={() => router.push(`/chat/alert/${alert.id}`)}
        />
        <View style={styles.half}>
          <CallButton target={{ scope: "ALERT", id: alert.id }} />
        </View>
      </View>

      <Card>
        <Text style={styles.section}>Response history</Text>
        {history.data && history.data.length > 0 ? (
          history.data.map((event, index) => (
            <View key={index} style={styles.event}>
              <View style={styles.eventDot} />
              <View style={{ flex: 1 }}>
                <Text style={styles.eventTitle}>
                  {EVENT_LABEL[event.event_type] ?? event.event_type}
                  {event.actor_name ? ` · ${event.actor_name}` : event.actor_role === "SYSTEM" ? " · System" : ""}
                </Text>
                <Text style={styles.hint}>{formatDateTime(event.created_at)}</Text>
              </View>
            </View>
          ))
        ) : (
          <Empty title="No history yet" />
        )}
      </Card>
    </ScrollView>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={styles.fieldValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16, gap: 14, paddingBottom: 120 },
  snapshot: { flexDirection: "row", gap: 12, alignItems: "center" },
  snapshotText: { flex: 1, fontSize: 13, color: colors.textMuted, lineHeight: 18 },
  badges: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  type: { fontSize: 22, fontWeight: "800", color: colors.text },
  field: { gap: 2 },
  fieldLabel: { fontSize: 12, fontWeight: "700", color: colors.textSubtle, textTransform: "uppercase" },
  fieldValue: { fontSize: 15, color: colors.text },
  note: { fontSize: 12, color: colors.textSubtle },
  section: { fontSize: 16, fontWeight: "800", color: colors.text },
  row: { flexDirection: "row", gap: 10 },
  half: { flex: 1 },
  hint: { fontSize: 13, color: colors.textMuted },
  responder: { flexDirection: "row", gap: 10, alignItems: "center" },
  responderName: { fontSize: 15, fontWeight: "600", color: colors.text },
  event: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  eventDot: { width: 10, height: 10, borderRadius: radius.pill, backgroundColor: colors.primary, marginTop: 5 },
  eventTitle: { fontSize: 14, fontWeight: "600", color: colors.text },
});
