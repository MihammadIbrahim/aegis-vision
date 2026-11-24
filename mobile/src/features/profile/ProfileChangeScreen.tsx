/**
 * Decision X self-service change: email, phone number and/or password. Saving needs the current password, then the
 * code sent to the registered email; a new email also needs the code sent to it. Nothing changes until the codes are
 * verified; cancelling or an expired code changes nothing. A new password signs out every session (sign in again).
 */
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { errorMessage } from "../../api/client";
import { guardApi } from "../../api/endpoints";
import type { GuardProfileChangeRequest, ProfileChangeStartedResponse } from "../../api/types";
import { useSession } from "../../auth/SessionProvider";
import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Header } from "../../components/Header";
import { OtpInput } from "../../components/OtpInput";
import { TextField } from "../../components/TextField";
import { qk } from "../../lib/queryKeys";
import { useCountdown } from "../../lib/useCountdown";
import { isEmail, isOtp, isPhone, passwordAcceptable } from "../../lib/validation";
import { colors } from "../../theme";
import { PasswordPolicy } from "../auth/ResetPasswordScreen";

export default function ProfileChangeScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { signOut } = useSession();
  const [currentPassword, setCurrentPassword] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [started, setStarted] = useState<ProfileChangeStartedResponse | null>(null);
  const [code, setCode] = useState("");
  const [newEmailCode, setNewEmailCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const countdown = useCountdown(0);

  const start = async () => {
    const body: GuardProfileChangeRequest = { current_password: currentPassword };
    if (email.trim()) body.email = email.trim();
    if (phone.trim()) body.phone_number = phone.trim();
    if (newPassword) {
      body.new_password = newPassword;
      body.confirm_new_password = confirmation;
    }
    if (!currentPassword) return setError("Enter your current password.");
    if (!body.email && !body.phone_number && !body.new_password) return setError("Change at least one field.");
    if (body.email && !isEmail(body.email)) return setError("Enter a valid new email address.");
    if (body.phone_number && !isPhone(body.phone_number)) return setError("Enter a valid phone number (7–15 digits).");
    if (body.new_password && !passwordAcceptable(newPassword, confirmation)) {
      return setError("The new password does not meet every rule yet.");
    }
    setBusy(true);
    setError(null);
    try {
      const response = await guardApi.startProfileChange(body);
      setStarted(response);
      countdown.restart(response.resend_available_in_seconds);
      setCurrentPassword("");
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    if (!started) return;
    if (!isOtp(code) || (started.new_email && !isOtp(newEmailCode))) {
      setError(started.new_email ? "Enter both 6-digit codes." : "Enter the 6-digit code.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const done = await guardApi.verifyProfileChange(started.change_id, {
        otp: code,
        new_email_otp: started.new_email ? newEmailCode : null,
      });
      if (done.signed_out) {
        await signOut({ code: "PASSWORD_CHANGED", message: "Password changed." });
        return;
      }
      queryClient.setQueryData(qk.profile, done.profile);
      void queryClient.invalidateQueries({ queryKey: qk.dashboard });
      setSaved(true);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (started) await guardApi.cancelProfileChange(started.change_id).catch(() => undefined);
    router.back();
  };

  const resend = async () => {
    if (!started) return;
    try {
      const again = await guardApi.resendProfileCodes(started.change_id);
      setStarted(again);
      countdown.restart(again.resend_available_in_seconds);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  };

  return (
    <View style={styles.flex}>
      <Header title="Update contact & security" subtitle="Email, phone number, password" back />
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        {error ? <Banner tone="danger" message={error} testID="change-error" /> : null}
        {saved ? (
          <Card testID="change-saved">
            <Banner tone="success" message="Your changes were saved." />
            <Button label="Back to profile" onPress={() => router.back()} />
          </Card>
        ) : !started ? (
          <Card>
            <Text style={styles.muted}>Leave a field empty to keep it. Name, username and Guard ID cannot be changed here.</Text>
            <TextField label="New email (optional)" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" testID="change-email" />
            <TextField label="New phone number (optional)" value={phone} onChangeText={setPhone} keyboardType="phone-pad" testID="change-phone" />
            <TextField label="New password (optional)" value={newPassword} onChangeText={setNewPassword} secure autoCapitalize="none" testID="change-new-password" />
            {newPassword ? (
              <>
                <TextField label="Confirm new password" value={confirmation} onChangeText={setConfirmation} secure autoCapitalize="none" testID="change-confirm" />
                <PasswordPolicy password={newPassword} confirmation={confirmation} />
              </>
            ) : null}
            <TextField label="Current password" value={currentPassword} onChangeText={setCurrentPassword} secure autoCapitalize="none" testID="change-current" />
            <Button testID="change-start" label="Save and send code" large loading={busy} onPress={() => void start()} />
            <Button variant="ghost" label="Cancel" onPress={() => router.back()} />
          </Card>
        ) : (
          <Card>
            <Text style={styles.muted}>Enter the code sent to your registered email {started.email}.</Text>
            <OtpInput value={code} onChange={setCode} disabled={busy} label="Code from your registered email" />
            {started.new_email ? (
              <>
                <Text style={styles.muted}>And the code sent to your new email {started.new_email}.</Text>
                <OtpInput value={newEmailCode} onChange={setNewEmailCode} disabled={busy} label="Code from your new email" />
              </>
            ) : null}
            <Button testID="change-verify" label="Verify and save" large loading={busy} onPress={() => void verify()} />
            <Button
              variant="ghost"
              label={countdown.seconds > 0 ? `Resend codes in 0:${String(countdown.seconds).padStart(2, "0")}` : "Resend codes"}
              disabled={countdown.seconds > 0}
              onPress={() => void resend()}
            />
            <Button testID="change-cancel" variant="ghost" label="Cancel (nothing changes)" onPress={() => void cancel()} />
          </Card>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16, gap: 14 },
  muted: { fontSize: 13, color: colors.textMuted, lineHeight: 18 },
});
