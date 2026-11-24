import { Ionicons } from "@expo/vector-icons";
import { forwardRef, useState, type ComponentProps, type ComponentRef } from "react";
import { Pressable, StyleSheet, Text, TextInput, View, type TextInputProps } from "react-native";

import { colors, radius, TOUCH_TARGET } from "../theme";

export interface TextFieldProps extends Omit<TextInputProps, "style"> {
  label: string;
  error?: string | null;
  hint?: string;
  icon?: ComponentProps<typeof Ionicons>["name"];
  secure?: boolean;
}

export const TextField = forwardRef<ComponentRef<typeof TextInput>, TextFieldProps>(function TextField(
  { label, error, hint, icon, secure = false, multiline, ...input },
  ref,
) {
  const [hidden, setHidden] = useState(true);
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <View style={[styles.box, multiline && styles.multiline, error ? styles.boxError : null]}>
        {icon ? <Ionicons name={icon} size={18} color={colors.textSubtle} style={styles.icon} /> : null}
        <TextInput
          ref={ref}
          accessibilityLabel={label}
          placeholderTextColor={colors.textSubtle}
          style={[styles.input, multiline && styles.multilineInput]}
          secureTextEntry={secure && hidden}
          multiline={multiline}
          autoCorrect={false}
          {...input}
        />
        {secure ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={hidden ? `Show ${label.toLowerCase()}` : `Hide ${label.toLowerCase()}`}
            onPress={() => setHidden((h) => !h)}
            hitSlop={12}
            style={styles.toggle}
          >
            <Ionicons name={hidden ? "eye-outline" : "eye-off-outline"} size={20} color={colors.textSubtle} />
          </Pressable>
        ) : null}
      </View>
      {error ? (
        <Text style={styles.error} accessibilityRole="alert">
          {error}
        </Text>
      ) : hint ? (
        <Text style={styles.hint}>{hint}</Text>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  field: { gap: 6 },
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
  },
  boxError: { borderColor: colors.danger },
  multiline: { alignItems: "flex-start", paddingVertical: 10 },
  icon: { marginRight: 8 },
  input: { flex: 1, fontSize: 15, color: colors.text, paddingVertical: 10 },
  multilineInput: { minHeight: 96, textAlignVertical: "top" },
  toggle: { padding: 6 },
  error: { fontSize: 13, color: colors.danger },
  hint: { fontSize: 12, color: colors.textSubtle },
});
