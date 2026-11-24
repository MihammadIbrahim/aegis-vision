import { type ComponentRef, useRef } from "react";
import { StyleSheet, TextInput, View } from "react-native";

import { colors, radius } from "../theme";

const LENGTH = 6;

/** Six digit boxes with auto-advance, backspace to the previous box, and paste of a whole code. */
export function OtpInput({
  value,
  onChange,
  disabled = false,
  label = "6-digit verification code",
}: {
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  label?: string;
}) {
  const refs = useRef<(ComponentRef<typeof TextInput> | null)[]>([]);
  const digits = Array.from({ length: LENGTH }, (_, i) => value[i] ?? "");

  const setAt = (index: number, text: string) => {
    const clean = text.replace(/[^0-9]/g, "");
    if (clean.length > 1) {
      // Pasted or auto-filled: spread the digits from this box on.
      const next = (value.slice(0, index) + clean).slice(0, LENGTH);
      onChange(next);
      refs.current[Math.min(next.length, LENGTH - 1)]?.focus();
      return;
    }
    const chars = digits.slice();
    chars[index] = clean;
    const next = chars.join("").slice(0, LENGTH);
    onChange(next);
    if (clean && index < LENGTH - 1) refs.current[index + 1]?.focus();
  };

  return (
    <View style={styles.row} accessibilityLabel={label}>
      {digits.map((digit, index) => (
        <TextInput
          key={index}
          ref={(r) => {
            refs.current[index] = r;
          }}
          testID={`otp-${index}`}
          accessibilityLabel={`Digit ${index + 1} of ${LENGTH}`}
          style={[styles.box, digit ? styles.filled : null]}
          value={digit}
          editable={!disabled}
          keyboardType="number-pad"
          textContentType="oneTimeCode"
          autoComplete="one-time-code"
          autoFocus={index === 0}
          maxLength={index === 0 ? LENGTH : 1}
          onChangeText={(text) => setAt(index, text)}
          onKeyPress={({ nativeEvent }) => {
            if (nativeEvent.key === "Backspace" && !digits[index] && index > 0) refs.current[index - 1]?.focus();
          }}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", justifyContent: "space-between", gap: 8 },
  box: {
    flex: 1,
    height: 56,
    borderRadius: radius.md,
    borderWidth: 1.5,
    borderColor: colors.border,
    backgroundColor: colors.inputBackground,
    textAlign: "center",
    fontSize: 22,
    fontWeight: "700",
    color: colors.text,
  },
  filled: { borderColor: colors.primary, backgroundColor: colors.surface },
});
