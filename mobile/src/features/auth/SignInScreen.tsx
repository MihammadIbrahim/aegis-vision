/**
 * Screen 1 — Guard Login (decisions D-017, D-018, V). Operational mode + Guard ID + registered email + password,
 * then the email code (screen 2). No organization name and no registration: Admins create Guard accounts.
 */
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ApiError, errorMessage } from "../../api/client";
import { authApi } from "../../api/endpoints";
import type { OperationalMode } from "../../api/types";
import { useAuthFlow } from "../../auth/AuthFlowProvider";
import { useSession } from "../../auth/SessionProvider";
import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { ModeSelector } from "../../components/ModeSelector";
import { TextField } from "../../components/TextField";
import { secureDelete, secureGet, secureSet } from "../../lib/secure";
import { SECURE_KEYS } from "../../lib/storageKeys";
import { isEmail, isGuardId, normalizeGuardId } from "../../lib/validation";
import { colors, radius } from "../../theme";

interface Remembered {
  guard_id: string;
  email: string;
  operational_mode: OperationalMode;
}

interface Errors {
  mode?: string;
  guardId?: string;
  email?: string;
  password?: string;
}

export function loginErrorMessage(error: unknown): { tone: "danger" | "warning"; message: string } {
  if (error instanceof ApiError) {
    if (error.code === "OPERATIONAL_MODE_MISMATCH") return { tone: "warning", message: error.message };
    if (error.status === 429) {
      const minutes = error.retryAfterSeconds ? Math.ceil(error.retryAfterSeconds / 60) : 15;
      return {
        tone: "danger",
        message: `Too many failed attempts. Sign-in is locked for about ${minutes} minute${minutes === 1 ? "" : "s"}.`,
      };
    }
    if (error.code === "INVALID_CREDENTIALS" || error.status === 401) {
      return {
        tone: "danger",
        message: "The Guard ID, email or password is incorrect, or this account is not active.",
      };
    }
  }
  return { tone: "danger", message: errorMessage(error) };
}

export default function SignInScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { state, clearNotice } = useSession();
  const { setPendingLogin } = useAuthFlow();
  const [mode, setMode] = useState<OperationalMode | null>(null);
  const [guardId, setGuardId] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [failure, setFailure] = useState<{ tone: "danger" | "warning"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const notice = state.status === "signedOut" ? state.notice : null;

  useEffect(() => {
    void secureGet<Remembered>(SECURE_KEYS.rememberedIdentifier).then((saved) => {
      if (!saved) return;
      setGuardId(saved.guard_id);
      setEmail(saved.email);
      setMode(saved.operational_mode);
      setRemember(true);
    });
  }, []);

  const submit = async () => {
    const found: Errors = {};
    if (!mode) found.mode = "Choose Standby or Stationed / Standalone.";
    if (!isGuardId(guardId)) found.guardId = "Enter your Guard ID (6 letters and 21 digits, or GRD- and 11 digits).";
    if (!isEmail(email)) found.email = "Enter your registered email address.";
    if (!password) found.password = "Enter your password.";
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length > 0 || !mode) return;
    setBusy(true);
    try {
      const body = { operational_mode: mode, guard_id: normalizeGuardId(guardId), email: email.trim(), password };
      const sent = await authApi.login(body);
      if (remember) {
        await secureSet(SECURE_KEYS.rememberedIdentifier, {
          guard_id: body.guard_id,
          email: body.email,
          operational_mode: mode,
        } satisfies Remembered);
      } else {
        await secureDelete(SECURE_KEYS.rememberedIdentifier);
      }
      setPassword("");
      clearNotice();
      setPendingLogin({ sent, mode });
      router.push("/verify");
    } catch (error) {
      setFailure(loginErrorMessage(error));
      if (error instanceof ApiError && error.code === "OPERATIONAL_MODE_MISMATCH") setErrors({ mode: error.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView style={styles.flex} contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <View style={[styles.hero, { paddingTop: insets.top + 24 }]}>
          <View style={styles.logo}>
            <Ionicons name="shield-checkmark" size={28} color={colors.onPrimary} />
          </View>
          <Text style={styles.heroTitle}>AI-Powered CCTV Surveillance</Text>
          <Text style={styles.heroSub}>Security Guard Portal</Text>
        </View>
        <View style={styles.card}>
          <Text style={styles.title} accessibilityRole="header">
            Sign in
          </Text>
          <Text style={styles.sub}>Guard accounts are created by your Organization Admin.</Text>
          {notice ? <Banner tone="warning" title={notice.title} message={notice.message} testID="session-notice" /> : null}
          {failure ? <Banner tone={failure.tone} message={failure.message} testID="login-error" /> : null}
          <ModeSelector value={mode} onChange={setMode} error={errors.mode} />
          <TextField
            label="Guard ID"
            icon="id-card-outline"
            value={guardId}
            onChangeText={setGuardId}
            autoCapitalize="characters"
            placeholder="e.g. ABCDEF123456789012345678901"
            error={errors.guardId}
            testID="input-guard-id"
          />
          <TextField
            label="Registered email"
            icon="mail-outline"
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            keyboardType="email-address"
            autoComplete="email"
            placeholder="name@organization.com"
            error={errors.email}
            testID="input-email"
          />
          <TextField
            label="Password"
            icon="lock-closed-outline"
            value={password}
            onChangeText={setPassword}
            secure
            autoCapitalize="none"
            autoComplete="password"
            placeholder="Your password"
            error={errors.password}
            testID="input-password"
          />
          <View style={styles.rememberRow}>
            <Text style={styles.rememberText}>Remember Guard ID, email and mode on this phone</Text>
            <Switch
              testID="remember-switch"
              accessibilityLabel="Remember login identifier"
              value={remember}
              onValueChange={setRemember}
              trackColor={{ true: colors.primary, false: colors.borderStrong }}
            />
          </View>
          <Pressable accessibilityRole="link" onPress={() => router.push("/forgot-password")} style={styles.forgot}>
            <Text style={styles.link}>Forgot password?</Text>
          </Pressable>
          <Button testID="sign-in" label="Sign in" icon="arrow-forward" large loading={busy} onPress={() => void submit()} />
        </View>
        <Text style={styles.footer}>Authorized security personnel only. Every sign-in needs a code from your email.</Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  scroll: { paddingBottom: 32 },
  hero: { backgroundColor: colors.navy, paddingHorizontal: 24, paddingBottom: 56, gap: 6 },
  logo: {
    width: 52,
    height: 52,
    borderRadius: radius.md,
    backgroundColor: colors.navyRaised,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 12,
  },
  heroTitle: { color: colors.textOnNavy, fontSize: 24, fontWeight: "800" },
  heroSub: { color: colors.textOnNavyMuted, fontSize: 14 },
  card: {
    marginTop: -32,
    marginHorizontal: 16,
    backgroundColor: colors.surface,
    borderRadius: radius.xl,
    padding: 20,
    gap: 14,
    borderWidth: 1,
    borderColor: colors.border,
  },
  title: { fontSize: 26, fontWeight: "800", color: colors.text },
  sub: { fontSize: 14, color: colors.textMuted },
  rememberRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  rememberText: { flex: 1, fontSize: 13, color: colors.textMuted },
  forgot: { alignSelf: "flex-end", paddingVertical: 4 },
  link: { color: colors.primary, fontWeight: "700", fontSize: 14 },
  footer: { textAlign: "center", color: colors.textSubtle, fontSize: 12, marginTop: 20, paddingHorizontal: 24 },
});
