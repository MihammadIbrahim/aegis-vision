/**
 * Screen 4 — Reset Password. Step 1: the emailed code returns a one-time reset token, kept in memory only. Step 2: the
 * new password twice, with the server's policy shown live; every earlier session is signed out, then back to sign-in.
 */
import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { Modal, ScrollView, StyleSheet, Text, View } from "react-native";

import { ApiError, errorMessage } from "../../api/client";
import { authApi } from "../../api/endpoints";
import { useAuthFlow } from "../../auth/AuthFlowProvider";
import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Header } from "../../components/Header";
import { OtpInput } from "../../components/OtpInput";
import { TextField } from "../../components/TextField";
import { useCountdown } from "../../lib/useCountdown";
import { isOtp, passwordAcceptable, passwordChecks } from "../../lib/validation";
import { colors, radius } from "../../theme";

export function PasswordPolicy({ password, confirmation }: { password: string; confirmation: string }) {
  return (
    <View style={styles.policy} accessibilityLabel="Password requirements">
      {passwordChecks(password, confirmation).map((check) => (
        <Text key={check.label} style={[styles.check, check.ok ? styles.ok : null]}>
          {check.ok ? "✓" : "•"} {check.label}
        </Text>
      ))}
      <Text style={styles.check}>• Must not contain your username or email (checked by the server)</Text>
    </View>
  );
}

export default function ResetPasswordScreen() {
  const router = useRouter();
  const { pendingReset, setPendingReset } = useAuthFlow();
  const [code, setCode] = useState("");
  const [resetToken, setResetToken] = useState<string | null>(null); // memory only
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const countdown = useCountdown(pendingReset?.started.resend_available_in_seconds ?? 60);

  useEffect(() => {
    if (!pendingReset && !done) router.replace("/forgot-password");
  }, [pendingReset, done, router]);
  if (!pendingReset) return null;
  const resetId = pendingReset.started.reset_id;

  const verify = async () => {
    if (!isOtp(code)) {
      setError("Enter the 6-digit code from your email.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const token = await authApi.verifyResetCode(resetId, code);
      setResetToken(token.reset_token);
    } catch (failure) {
      setError(errorMessage(failure));
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  const complete = async () => {
    if (!resetToken || !passwordAcceptable(password, confirmation)) {
      setError("The new password does not meet every rule yet.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await authApi.completeReset(resetId, {
        reset_token: resetToken,
        new_password: password,
        confirm_new_password: confirmation,
      });
      setResetToken(null);
      setPassword("");
      setConfirmation("");
      setDone(true);
    } catch (failure) {
      setError(errorMessage(failure));
      if (failure instanceof ApiError && (failure.status === 400 || failure.status === 410)) setResetToken(null);
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    try {
      const again = await authApi.resendResetCode(resetId);
      countdown.restart(again.resend_available_in_seconds);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  };

  return (
    <View style={styles.flex}>
      <Header title={resetToken ? "Create new password" : "Verify reset code"} subtitle="Account recovery" back />
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        <Card>
          {error ? <Banner tone="danger" message={error} testID="reset-error" /> : null}
          {!resetToken ? (
            <>
              <Text style={styles.text}>
                If the details matched an account, a 6-digit code was emailed to {pendingReset.maskedEmail}.
              </Text>
              <OtpInput value={code} onChange={setCode} disabled={busy} />
              <Button testID="verify-reset" label="Verify code" large loading={busy} onPress={() => void verify()} />
              <Button
                variant="ghost"
                label={countdown.seconds > 0 ? `Resend code in 0:${String(countdown.seconds).padStart(2, "0")}` : "Resend code"}
                disabled={countdown.seconds > 0}
                onPress={() => void resend()}
              />
            </>
          ) : (
            <>
              <TextField label="New password" secure value={password} onChangeText={setPassword} autoCapitalize="none" testID="new-password" />
              <TextField
                label="Confirm new password"
                secure
                value={confirmation}
                onChangeText={setConfirmation}
                autoCapitalize="none"
                testID="confirm-password"
              />
              <PasswordPolicy password={password} confirmation={confirmation} />
              <Button testID="complete-reset" label="Reset password" large loading={busy} onPress={() => void complete()} />
            </>
          )}
        </Card>
      </ScrollView>
      <Modal visible={done} transparent animationType="fade" onRequestClose={() => undefined}>
        <View style={styles.backdrop}>
          <View style={styles.modal} testID="reset-done">
            <Text style={styles.modalTitle}>Password changed</Text>
            <Text style={styles.text}>Every earlier session was signed out. Sign in with your new password.</Text>
            <Button
              testID="back-to-sign-in"
              label="Go to sign in"
              onPress={() => {
                setDone(false);
                setPendingReset(null);
                router.replace("/sign-in");
              }}
            />
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16 },
  text: { fontSize: 14, color: colors.textMuted, lineHeight: 20 },
  policy: { gap: 4, backgroundColor: colors.inputBackground, borderRadius: radius.md, padding: 12 },
  check: { fontSize: 13, color: colors.textMuted },
  ok: { color: colors.success, fontWeight: "600" },
  backdrop: { flex: 1, backgroundColor: colors.overlay, justifyContent: "center", padding: 24 },
  modal: { backgroundColor: colors.surface, borderRadius: radius.xl, padding: 20, gap: 14 },
  modalTitle: { fontSize: 20, fontWeight: "800", color: colors.text },
});
