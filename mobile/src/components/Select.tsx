import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { FlatList, Modal, Pressable, StyleSheet, Text, View } from "react-native";

import { colors, radius, TOUCH_TARGET } from "../theme";

export interface SelectOption {
  value: string;
  label: string;
}

/** A dropdown that opens a list in a sheet (works the same on Android and iOS). */
export function Select({
  label,
  value,
  options,
  onChange,
  placeholder = "Select…",
  disabled = false,
  error,
  testID,
}: {
  label: string;
  value: string | null;
  options: SelectOption[];
  onChange: (value: string | null) => void;
  placeholder?: string;
  disabled?: boolean;
  error?: string | null;
  testID?: string;
}) {
  const [open, setOpen] = useState(false);
  const selected = options.find((o) => o.value === value) ?? null;
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${selected ? selected.label : placeholder}`}
        accessibilityState={{ disabled }}
        disabled={disabled}
        onPress={() => setOpen(true)}
        style={[styles.box, disabled && styles.disabled, error ? styles.boxError : null]}
      >
        <Text style={[styles.value, !selected && styles.placeholder]} numberOfLines={1}>
          {selected ? selected.label : placeholder}
        </Text>
        <Ionicons name="chevron-down" size={18} color={colors.textSubtle} />
      </Pressable>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Modal visible={open} transparent animationType="slide" onRequestClose={() => setOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setOpen(false)} accessibilityLabel="Close list" />
        <View style={styles.sheet}>
          <Text style={styles.sheetTitle}>{label}</Text>
          <FlatList
            data={options}
            keyExtractor={(o) => o.value}
            renderItem={({ item }) => (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ selected: item.value === value }}
                onPress={() => {
                  onChange(item.value);
                  setOpen(false);
                }}
                style={styles.option}
              >
                <Text style={[styles.optionText, item.value === value && styles.optionSelected]}>{item.label}</Text>
                {item.value === value ? <Ionicons name="checkmark" size={18} color={colors.primary} /> : null}
              </Pressable>
            )}
            ListEmptyComponent={<Text style={styles.empty}>Nothing to choose from.</Text>}
          />
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  field: { gap: 6, flex: 1 },
  label: { fontSize: 13, fontWeight: "700", color: colors.textMuted },
  box: {
    minHeight: TOUCH_TARGET + 4,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.inputBackground,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    gap: 8,
  },
  boxError: { borderColor: colors.danger },
  disabled: { opacity: 0.5 },
  value: { flex: 1, fontSize: 15, color: colors.text },
  placeholder: { color: colors.textSubtle },
  error: { fontSize: 13, color: colors.danger },
  backdrop: { flex: 1, backgroundColor: colors.overlay },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    padding: 16,
    maxHeight: "60%",
  },
  sheetTitle: { fontSize: 16, fontWeight: "700", color: colors.text, marginBottom: 8 },
  option: {
    minHeight: TOUCH_TARGET,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  optionText: { fontSize: 15, color: colors.text },
  optionSelected: { color: colors.primary, fontWeight: "700" },
  empty: { color: colors.textMuted, paddingVertical: 12 },
});
