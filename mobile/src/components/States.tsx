import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { errorMessage } from "../api/client";
import { colors } from "../theme";
import { Button } from "./Button";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <View style={styles.center} accessibilityLabel={label}>
      <ActivityIndicator color={colors.primary} size="large" />
      <Text style={styles.muted}>{label}</Text>
    </View>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <View style={styles.center}>
      <Text style={styles.error} accessibilityRole="alert">
        {errorMessage(error)}
      </Text>
      {onRetry ? <Button label="Try again" variant="secondary" onPress={onRetry} /> : null}
    </View>
  );
}

export function Empty({ title, message }: { title: string; message?: string }) {
  return (
    <View style={styles.center}>
      <Text style={styles.title}>{title}</Text>
      {message ? <Text style={styles.muted}>{message}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  center: { padding: 24, alignItems: "center", justifyContent: "center", gap: 12 },
  title: { fontSize: 16, fontWeight: "700", color: colors.text, textAlign: "center" },
  muted: { fontSize: 14, color: colors.textMuted, textAlign: "center" },
  error: { fontSize: 14, color: colors.danger, textAlign: "center" },
});
