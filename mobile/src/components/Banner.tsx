import { Ionicons } from "@expo/vector-icons";
import type { ComponentProps } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { colors, radius } from "../theme";
import type { Tone } from "./Badge";

const TONES: Record<Tone, { bg: string; fg: string; border: string; icon: ComponentProps<typeof Ionicons>["name"] }> = {
  danger: { bg: colors.dangerTint, fg: colors.danger, border: colors.dangerBorder, icon: "alert-circle" },
  warning: { bg: colors.warningTint, fg: colors.warning, border: colors.warningBorder, icon: "warning" },
  success: { bg: colors.successTint, fg: colors.success, border: colors.successTint, icon: "checkmark-circle" },
  info: { bg: colors.infoTint, fg: colors.info, border: colors.primaryBorder, icon: "information-circle" },
  primary: { bg: colors.primaryTint, fg: colors.primary, border: colors.primaryBorder, icon: "information-circle" },
  neutral: { bg: colors.surface, fg: colors.textMuted, border: colors.border, icon: "information-circle" },
};

export function Banner({
  tone = "info",
  title,
  message,
  onPress,
  testID,
}: {
  tone?: Tone;
  title?: string;
  message: string;
  onPress?: () => void;
  testID?: string;
}) {
  const palette = TONES[tone];
  const content = (
    <View
      testID={testID}
      accessibilityRole={tone === "danger" ? "alert" : undefined}
      style={[styles.banner, { backgroundColor: palette.bg, borderColor: palette.border }]}
    >
      <Ionicons name={palette.icon} size={20} color={palette.fg} />
      <View style={styles.texts}>
        {title ? <Text style={[styles.title, { color: palette.fg }]}>{title}</Text> : null}
        <Text style={[styles.message, { color: palette.fg }]}>{message}</Text>
      </View>
    </View>
  );
  return onPress ? (
    <Pressable accessibilityRole="button" onPress={onPress}>
      {content}
    </Pressable>
  ) : (
    content
  );
}

const styles = StyleSheet.create({
  banner: { flexDirection: "row", gap: 10, padding: 12, borderRadius: radius.md, borderWidth: 1, alignItems: "flex-start" },
  texts: { flex: 1, gap: 2 },
  title: { fontSize: 14, fontWeight: "700" },
  message: { fontSize: 13, lineHeight: 18 },
});
