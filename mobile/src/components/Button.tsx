import { Ionicons } from "@expo/vector-icons";
import type { ComponentProps } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";

import { colors, radius, TOUCH_TARGET } from "../theme";

type Variant = "primary" | "secondary" | "danger" | "dangerSoft" | "success" | "outline" | "ghost";

const STYLES: Record<Variant, { bg: string; fg: string; border: string }> = {
  primary: { bg: colors.primary, fg: colors.onPrimary, border: colors.primary },
  secondary: { bg: colors.primaryTint, fg: colors.primary, border: colors.primaryTint },
  danger: { bg: colors.danger, fg: colors.onPrimary, border: colors.danger },
  dangerSoft: { bg: colors.dangerTint, fg: colors.danger, border: colors.dangerBorder },
  success: { bg: colors.successTint, fg: colors.success, border: colors.successTint },
  outline: { bg: colors.surface, fg: colors.primary, border: colors.primary },
  ghost: { bg: colors.transparent, fg: colors.primary, border: colors.transparent },
};

export interface ButtonProps {
  label: string;
  onPress: () => void;
  variant?: Variant;
  icon?: ComponentProps<typeof Ionicons>["name"];
  loading?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
  testID?: string;
  accessibilityHint?: string;
  large?: boolean;
}

export function Button({
  label,
  onPress,
  variant = "primary",
  icon,
  loading = false,
  disabled = false,
  style,
  testID,
  accessibilityHint,
  large = false,
}: ButtonProps) {
  const palette = STYLES[variant];
  const inactive = disabled || loading;
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: inactive, busy: loading }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [
        styles.base,
        large && styles.large,
        { backgroundColor: palette.bg, borderColor: palette.border, opacity: inactive ? 0.55 : pressed ? 0.85 : 1 },
        style,
      ]}
    >
      <View style={styles.row}>
        {loading ? (
          <ActivityIndicator color={palette.fg} />
        ) : icon ? (
          <Ionicons name={icon} size={18} color={palette.fg} />
        ) : null}
        <Text style={[styles.label, large && styles.largeLabel, { color: palette.fg }]}>{label}</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: TOUCH_TARGET,
    borderRadius: radius.md,
    borderWidth: 1.5,
    paddingHorizontal: 16,
    alignItems: "center",
    justifyContent: "center",
  },
  large: { minHeight: 56 },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  label: { fontSize: 15, fontWeight: "700" },
  largeLabel: { fontSize: 17 },
});
