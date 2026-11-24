/**
 * Screen 2 — Email OTP Verification. Six auto-advancing digit boxes, the masked email, a resend countdown from the
 * server (60 s), and verification with the operational mode again (the server re-checks the assignment). The token
 * goes only to expo-secure-store; the app then opens the dashboard the server named.
 */
import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { ApiError, errorMessage } from "../../api/client";
import { authApi } from "../../api/endpoints";
import { useAuthFlow } from "../../auth/AuthFlowProvider";
import { useSession } from "../../auth/SessionProvider";
import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Header } from "../../components/Header";
import { OtpInput } from "../../components/OtpInput";
import { MODE_LABEL } from "../../lib/format";
import { useCountdown } from "../../lib/useCountdown";
import { isOtp } from "../../lib/validation";
import { colors } from "../../theme";

export function codeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "OPERATIONAL_MODE_MISMATCH") return error.message;
    if (error.status === 400 || error.status === 410) return `${error.message} Check the code, or request a new one.`;
    if (error.status === 429) return "Too many attempts or codes. Wait a little, then request a new code.";
  }
  return errorMessage(error);
}

export default function VerifyScreen() {
  const router = useRouter();
  const { pendingLogin, setPendingLogin } = useAuthFlow();
  const { signIn } = useSession();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [masked, setMasked] = useState(pendingLogin?.sent.email ?? "");
  const countdown = useCountdown(pendingLogin?.sent.resend_available_in_seconds ?? 60);

  useEffect(() => {
    if (!pendingLogin) router.replace("/sign-in");
  }, [pendingLogin, router]);
  if (!pendingLogin) return null;

  const verify = async () => {
    if (!isOtp(code)) {
      setError("Enter the 6-digit code from your email.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const token = await authApi.verifyLogin(pendingLogin.sent.login_id, {
        otp: code,
        operational_mode: pendingLogin.mode,
      });
      await signIn(token);
      setPendingLogin(null);
      router.replace("/alerts");
    } catch (failure) {
      setError(codeErrorMessage(failure));
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setError(null);
    try {
      const sent = await authApi.resendLoginCode(pendingLogin.sent.login_id);
      setMasked(sent.email);
      countdown.restart(sent.resend_available_in_seconds);
      setInfo(`A new code was sent to ${sent.email}.`);
    } catch (failure) {
      if (failure instanceof ApiError && failure.retryAfterSeconds) countdown.restart(failure.retryAfterSeconds);
      setError(codeErrorMessage(failure));
    }
  };

  const minutes = Math.round(pendingLogin.sent.otp_expires_in_seconds / 60);
  return (
    <View style={styles.flex}>
      <Header title="Verification code" subtitle="Two-step sign-in" back />
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        <Card>
          <Text style={styles.title}>Enter the 6-digit code</Text>
          <Text style={styles.text} testID="masked-email">
            We sent a code to your registered email {masked}. It expires in {minutes} minutes and can be tried 5 times.
          </Text>
          <Text style={styles.mode}>Operational mode: {MODE_LABEL[pendingLogin.mode]}</Text>
          {error ? <Banner tone="danger" message={error} testID="verify-error" /> : null}
          {info && !error ? <Banner tone="success" message={info} /> : null}
          <OtpInput value={code} onChange={setCode} disabled={busy} />
          <Button testID="verify" label="Verify & Continue" icon="arrow-forward" large loading={busy} onPress={() => void verify()} />
          <Button
            testID="resend"
            variant="ghost"
            label={countdown.seconds > 0 ? `Resend code in 0:${String(countdown.seconds).padStart(2, "0")}` : "Resend code"}
            disabled={countdown.seconds > 0}
            onPress={() => void resend()}
          />
        </Card>
        <Text style={styles.footer}>No code? Check your spam folder, or wait for the countdown and resend.</Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16, gap: 16 },
  title: { fontSize: 22, fontWeight: "800", color: colors.text },
  text: { fontSize: 14, color: colors.textMuted, lineHeight: 20 },
  mode: { fontSize: 13, fontWeight: "600", color: colors.text },
  footer: { fontSize: 12, color: colors.textSubtle, textAlign: "center" },
});
