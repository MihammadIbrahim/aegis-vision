/**
 * Screen 3 — Forgot Password (decisions W, D-018). Operational mode + Guard ID + registered email. The answer is the
 * same whether or not they match an account (and whether or not the mode matches), so nothing is revealed.
 */
import { useRouter } from "expo-router";
import { useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text } from "react-native";

import { errorMessage } from "../../api/client";
import { authApi } from "../../api/endpoints";
import type { OperationalMode } from "../../api/types";
import { maskEmail, useAuthFlow } from "../../auth/AuthFlowProvider";
import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Header } from "../../components/Header";
import { ModeSelector } from "../../components/ModeSelector";
import { TextField } from "../../components/TextField";
import { isEmail, isGuardId, normalizeGuardId } from "../../lib/validation";
import { colors } from "../../theme";

export default function ForgotPasswordScreen() {
  const router = useRouter();
  const { setPendingReset } = useAuthFlow();
  const [mode, setMode] = useState<OperationalMode | null>(null);
  const [guardId, setGuardId] = useState("");
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<{ mode?: string; guardId?: string; email?: string }>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const found: typeof errors = {};
    if (!mode) found.mode = "Choose your current operational mode.";
    if (!isGuardId(guardId)) found.guardId = "Enter your Guard ID.";
    if (!isEmail(email)) found.email = "Enter a valid email address.";
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length > 0 || !mode) return;
    setBusy(true);
    try {
      const started = await authApi.requestReset({
        guard_id: normalizeGuardId(guardId),
        email: email.trim(),
        operational_mode: mode,
      });
      setPendingReset({ started, maskedEmail: maskEmail(email) });
      router.push("/reset-password");
    } catch (error) {
      setFailure(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <Header title="Forgot password" subtitle="Account recovery" back />
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        <Card>
          <Text style={styles.text}>
            Enter your operational mode, Guard ID and registered email. If they match your account, a reset code is
            emailed to you. No Admin or Supervisor approval is needed.
          </Text>
          {failure ? <Banner tone="danger" message={failure} /> : null}
          <ModeSelector value={mode} onChange={setMode} error={errors.mode} />
          <TextField
            label="Guard ID"
            icon="id-card-outline"
            value={guardId}
            onChangeText={setGuardId}
            autoCapitalize="characters"
            error={errors.guardId}
            testID="reset-guard-id"
          />
          <TextField
            label="Registered email"
            icon="mail-outline"
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            keyboardType="email-address"
            error={errors.email}
            testID="reset-email"
          />
          <Button testID="send-reset" label="Send reset code" icon="arrow-forward" large loading={busy} onPress={() => void submit()} />
          <Button variant="ghost" label="Back to sign in" onPress={() => router.replace("/sign-in")} />
        </Card>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16 },
  text: { fontSize: 14, color: colors.textMuted, lineHeight: 20 },
});
