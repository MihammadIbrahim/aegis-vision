import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Banner } from "../components/Banner";
import { PriorityBadge } from "../components/Badge";
import { Button } from "../components/Button";
import {
  ALERT_TYPE_LABEL,
  REASON_LABEL,
  alertLocationText,
  confidenceText,
  formatDateTime,
  myRecipient,
  timeAgo,
} from "../lib/format";
import { colors, radius } from "../theme";
import { primaryActionFor, useAlarm } from "./AlarmProvider";

/** The full-screen alarm. Cannot be closed by tapping outside or by the back button. */
export function AlarmOverlay() {
  const alarm = useAlarm();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const alert = alarm.pending[0];
  if (!alarm.ringing || !alert) return null;

  if (alarm.minimized) {
    return (
      <Pressable
        testID="alarm-banner"
        accessibilityRole="button"
        accessibilityLabel="Alarm ringing. Open to acknowledge."
        onPress={alarm.restore}
        style={[styles.banner, { paddingTop: insets.top + 6 }]}
      >
        <Ionicons name="notifications" size={20} color={colors.onPrimary} />
        <Text style={styles.bannerText} numberOfLines={2}>
          Alarm ringing · {ALERT_TYPE_LABEL[alert.alert_type]} at {alert.camera_name}. Tap to acknowledge.
        </Text>
      </Pressable>
    );
  }

  const primary = primaryActionFor(alert);
  const busy = alarm.busyAlertId === alert.id;
  const confidence = confidenceText(alert.confidence);
  const reason = myRecipient(alert)?.reason;

  return (
    <Modal
      testID="alarm-modal-root"
      visible
      animationType="fade"
      statusBarTranslucent
      // The back button must not silence an unacknowledged alert.
      onRequestClose={() => undefined}
    >
      <View testID="alarm-modal" style={[styles.screen, { paddingTop: insets.top + 16, paddingBottom: insets.bottom + 16 }]}>
        <View style={styles.top}>
          <View style={styles.pulse}>
            <Ionicons name="warning" size={40} color={colors.onPrimary} />
          </View>
          <Text style={styles.kicker} accessibilityRole="header">
            UNACKNOWLEDGED SECURITY ALERT
          </Text>
          {alarm.pending.length > 1 ? (
            <Text style={styles.more}>+{alarm.pending.length - 1} more waiting for you</Text>
          ) : null}
        </View>
        <ScrollView contentContainerStyle={styles.card}>
          <PriorityBadge priority={alert.priority} />
          <Text style={styles.type}>{ALERT_TYPE_LABEL[alert.alert_type]}</Text>
          {reason ? <Text style={styles.reason}>{REASON_LABEL[reason]}</Text> : null}
          <Row icon="videocam" label="Camera" value={alert.camera_name} />
          <Row icon="location" label="Location" value={alertLocationText(alert.location)} />
          <Row icon="time" label="Detected" value={`${formatDateTime(alert.detected_at)} · ${timeAgo(alert.detected_at)}`} />
          {confidence ? <Row icon="analytics" label="AI confidence" value={confidence} /> : null}
          <Text style={styles.note}>An AI detection is a signal for you to check, not proof.</Text>
        </ScrollView>
        {alarm.error ? <Banner tone="danger" message={alarm.error} testID="alarm-error" /> : null}
        <View style={styles.actions}>
          <Button
            testID="alarm-primary"
            large
            label={primary === "RESPONDING" ? "I'm Responding" : "Acknowledge Alert"}
            icon={primary === "RESPONDING" ? "navigate" : "checkmark-circle"}
            loading={busy}
            onPress={() => void alarm.confirm(alert, primary)}
          />
          {primary === "RESPONDING" ? (
            <Button
              testID="alarm-acknowledge"
              variant="secondary"
              label="Acknowledge Alert"
              disabled={busy}
              onPress={() => void alarm.confirm(alert, "ACKNOWLEDGE")}
            />
          ) : null}
          <Button
            testID="alarm-details"
            variant="ghost"
            label="View details (keeps ringing)"
            disabled={busy}
            onPress={() => {
              alarm.minimize();
              router.push(`/alert/${alert.id}`);
            }}
          />
        </View>
      </View>
    </Modal>
  );
}

function Row({ icon, label, value }: { icon: "videocam" | "location" | "time" | "analytics"; label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Ionicons name={icon} size={18} color={colors.textMuted} />
      <View style={{ flex: 1 }}>
        <Text style={styles.rowLabel}>{label}</Text>
        <Text style={styles.rowValue}>{value}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.navy, paddingHorizontal: 20, gap: 16 },
  top: { alignItems: "center", gap: 8 },
  pulse: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: colors.dangerStrong,
    alignItems: "center",
    justifyContent: "center",
  },
  kicker: { color: colors.textOnNavy, fontSize: 16, fontWeight: "800", letterSpacing: 1 },
  more: { color: colors.textOnNavyMuted, fontSize: 13 },
  card: { backgroundColor: colors.surface, borderRadius: radius.lg, padding: 18, gap: 12 },
  type: { fontSize: 24, fontWeight: "800", color: colors.text },
  reason: { fontSize: 14, fontWeight: "600", color: colors.danger },
  row: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  rowLabel: { fontSize: 12, color: colors.textMuted, fontWeight: "600" },
  rowValue: { fontSize: 15, color: colors.text },
  note: { fontSize: 12, color: colors.textSubtle },
  actions: { gap: 10 },
  banner: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    zIndex: 1000,
    elevation: 20,
    backgroundColor: colors.dangerStrong,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 16,
    paddingBottom: 10,
  },
  bannerText: { flex: 1, color: colors.onPrimary, fontWeight: "700", fontSize: 13 },
});
