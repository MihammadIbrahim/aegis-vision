import { StyleSheet, Text, View } from "react-native";

import type { AlertPriority, AlertStatus } from "../api/types";
import { PRIORITY_LABEL, STATUS_LABEL } from "../lib/format";
import { colors, radius } from "../theme";

export type Tone = "danger" | "warning" | "success" | "info" | "primary" | "neutral";

const TONES: Record<Tone, { bg: string; fg: string }> = {
  danger: { bg: colors.dangerTint, fg: colors.danger },
  warning: { bg: colors.warningTint, fg: colors.warning },
  success: { bg: colors.successTint, fg: colors.success },
  info: { bg: colors.infoTint, fg: colors.info },
  primary: { bg: colors.primaryTint, fg: colors.primary },
  neutral: { bg: colors.border, fg: colors.textMuted },
};

export function Badge({ label, tone = "neutral", testID }: { label: string; tone?: Tone; testID?: string }) {
  const palette = TONES[tone];
  return (
    <View testID={testID} style={[styles.badge, { backgroundColor: palette.bg }]}>
      <Text style={[styles.text, { color: palette.fg }]}>{label}</Text>
    </View>
  );
}

export function PriorityBadge({ priority }: { priority: AlertPriority }) {
  return (
    <Badge
      testID={`priority-${priority}`}
      label={PRIORITY_LABEL[priority].toUpperCase()}
      tone={priority === "HIGH" ? "danger" : "warning"}
    />
  );
}

const STATUS_TONE: Record<AlertStatus, Tone> = {
  NEW: "primary",
  ACKNOWLEDGED: "warning",
  RESPONDING: "success",
  ESCALATED: "danger",
  RESOLVED: "neutral",
  DISMISSED: "neutral",
};

export function StatusBadge({ status }: { status: AlertStatus }) {
  return <Badge label={STATUS_LABEL[status]} tone={STATUS_TONE[status]} />;
}

const styles = StyleSheet.create({
  badge: { alignSelf: "flex-start", borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 4 },
  text: { fontSize: 12, fontWeight: "700", letterSpacing: 0.3 },
});
