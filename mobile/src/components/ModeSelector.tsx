import { Ionicons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { OperationalMode } from "../api/types";
import { colors, radius } from "../theme";

const OPTIONS: { value: OperationalMode; title: string; detail: string; icon: "flash" | "location" }[] = [
  { value: "STANDBY", title: "Standby", detail: "No fixed post: escalations and rapid response", icon: "flash" },
  {
    value: "STATIONED",
    title: "Stationed / Standalone",
    detail: "Assigned to a building, floor or area",
    icon: "location",
  },
];

/** The operational mode chosen at sign-in and forgot password; the server checks it against the assignment. */
export function ModeSelector({
  value,
  onChange,
  error,
}: {
  value: OperationalMode | null;
  onChange: (mode: OperationalMode) => void;
  error?: string | null;
}) {
  return (
    <View style={styles.group} accessibilityRole="radiogroup" accessibilityLabel="Operational mode">
      <Text style={styles.label}>Operational mode</Text>
      <View style={styles.row}>
        {OPTIONS.map((option) => {
          const selected = value === option.value;
          return (
            <Pressable
              key={option.value}
              testID={`mode-${option.value}`}
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              accessibilityLabel={option.title}
              onPress={() => onChange(option.value)}
              style={[styles.option, selected && styles.selected]}
            >
              <Ionicons name={option.icon} size={20} color={selected ? colors.primary : colors.textSubtle} />
              <Text style={[styles.title, selected && styles.titleSelected]}>{option.title}</Text>
              <Text style={styles.detail}>{option.detail}</Text>
            </Pressable>
          );
        })}
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  group: { gap: 6 },
  label: { fontSize: 13, fontWeight: "700", color: colors.textMuted },
  row: { flexDirection: "row", gap: 10 },
  option: {
    flex: 1,
    borderWidth: 1.5,
    borderColor: colors.border,
    backgroundColor: colors.inputBackground,
    borderRadius: radius.md,
    padding: 12,
    gap: 4,
    minHeight: 96,
  },
  selected: { borderColor: colors.primary, backgroundColor: colors.primaryTint },
  title: { fontSize: 14, fontWeight: "700", color: colors.text },
  titleSelected: { color: colors.primary },
  detail: { fontSize: 12, color: colors.textMuted, lineHeight: 16 },
  error: { fontSize: 13, color: colors.danger },
});
