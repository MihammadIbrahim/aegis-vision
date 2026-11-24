/**
 * Screen 10 — Guard Profile & Settings. Read-only identity and assignment (managed by Admins and Supervisors),
 * the permitted self-service changes (email, phone, password; decision X), an alarm test, honest connection
 * diagnostics, and sign-out (stops the alarm and any call, forgets the push token, clears the encrypted session).
 */
import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { guardApi } from "../../api/endpoints";
import { useAlarm } from "../../alarm/AlarmProvider";
import { useSession, useSignedIn } from "../../auth/SessionProvider";
import { useCalls } from "../../calls/CallProvider";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Header } from "../../components/Header";
import { API_BASE_URL } from "../../config";
import { MODE_LABEL, ownLocationText, timeAgo } from "../../lib/format";
import { useOnline } from "../../lib/network";
import { qk } from "../../lib/queryKeys";
import { usePushStatus } from "../../notifications/PushStatus";
import { useOutbox } from "../../offline/OutboxProvider";
import { useRealtime } from "../../realtime/RealtimeProvider";
import { colors, radius } from "../../theme";

export default function ProfileScreen() {
  const router = useRouter();
  const session = useSignedIn();
  const { signOut } = useSession();
  const alarm = useAlarm();
  const calls = useCalls();
  const realtime = useRealtime();
  const online = useOnline();
  const outbox = useOutbox();
  const push = usePushStatus();
  const profile = useQuery({ queryKey: qk.profile, queryFn: guardApi.profile });
  const location = useQuery({ queryKey: qk.location, queryFn: guardApi.location });
  const status = useQuery({ queryKey: qk.realtimeStatus, queryFn: guardApi.realtimeStatus, retry: false });
  const p = profile.data;
  const mode = p?.operational_mode ?? session.mode;

  const socketText: Record<string, string> = {
    open: "Connected (live)",
    connecting: "Connecting…",
    reconnecting: "Reconnecting… (polling meanwhile)",
    closed: "Closed (polling)",
    idle: "Not started",
  };

  return (
    <View style={styles.flex}>
      <Header title="Guard Profile & Settings" subtitle="Your identity, post and account" />
      <ScrollView contentContainerStyle={styles.body}>
        <Card testID="profile-card">
          <View style={styles.identity}>
            <View style={styles.avatar}>
              <Ionicons name="shield-checkmark" size={30} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.name}>{p?.full_name ?? "…"}</Text>
              <Text style={styles.muted}>Guard ID · {p?.guard_id ?? "…"}</Text>
              <View style={styles.badges}>
                <Badge label={p?.status === "LEFT" ? "Left" : "Active"} tone="success" />
                <Badge label={MODE_LABEL[mode]} tone="primary" />
              </View>
            </View>
          </View>
          <Field label="Username" value={p?.username ?? "—"} locked />
          <Field label="Registered email" value={p?.email ?? "—"} />
          <Field label="Phone number" value={p?.phone_number ?? "—"} />
          <Field label="Organization" value={p ? `${p.organization_name} (${p.organization_id})` : "—"} locked />
          <Field label="Campus" value={p?.campus_name ?? "—"} locked />
          <Field label="Operational category" value={MODE_LABEL[mode]} locked />
          <Field label="Assigned location" value={ownLocationText(location.data)} locked />
          <View style={styles.notice}>
            <Ionicons name="lock-closed" size={16} color={colors.textMuted} />
            <Text style={styles.noticeText}>
              Name, username, Guard ID, organization, campus and your post are managed by your Admin or Supervisor. You
              cannot change your operational mode or post, or regenerate your Guard ID, from the app.
            </Text>
          </View>
        </Card>

        <Card>
          <Text style={styles.section}>Update contact & security</Text>
          <Text style={styles.muted}>Email, phone number and password. Needs your current password and a code from your email.</Text>
          <Button testID="open-profile-change" variant="outline" icon="create-outline" label="Change email, phone or password" onPress={() => router.push("/profile-change")} />
        </Card>

        <Card testID="alarm-test-card">
          <Text style={styles.section}>Alert alarm</Text>
          <Text style={styles.muted}>
            Plays the alarm siren for 3 seconds with vibration. Check that you can hear it before your shift; raise the
            alarm volume if needed.
          </Text>
          <Button
            testID="alarm-test"
            variant="dangerSoft"
            icon="volume-high"
            label={alarm.testing ? "Playing…" : "Test alarm sound"}
            disabled={alarm.testing || alarm.ringing}
            onPress={() => void alarm.test(3)}
          />
        </Card>

        <Card testID="diagnostics">
          <Text style={styles.section}>Network & connection status</Text>
          <Diag label="Internet" value={online ? "Online" : "Offline"} good={online} />
          <Diag label="Core API" value={status.isSuccess ? "Reachable" : status.isError ? "Not reachable" : "Checking…"} good={status.isSuccess} detail={API_BASE_URL} />
          <Diag label="Real-time (WebSocket)" value={socketText[realtime.status] ?? realtime.status} good={realtime.live} detail={realtime.lastEventAt ? `Last signal ${timeAgo(realtime.lastEventAt)}` : undefined} />
          <Diag
            label="Voice calls"
            value={
              status.data
                ? status.data.voice_calls_available
                  ? calls.voiceAvailable
                    ? "Available (LiveKit)"
                    : "Server ready; this build has no native audio"
                  : "Not configured on the server"
                : "Unknown"
            }
            good={Boolean(status.data?.voice_calls_available && calls.voiceAvailable)}
          />
          <Diag
            label="Push to locked phone"
            value={
              push.status === "registered"
                ? push.pushDeliveryMode === "fcm"
                  ? `Registered (${push.provider}); server sends pushes`
                  : `Registered (${push.provider}); server push is off`
                : push.status === "unavailable"
                  ? push.reason
                  : "Checking…"
            }
            good={push.status === "registered" && push.pushDeliveryMode === "fcm"}
          />
          <Diag label="Last report sync" value={outbox.lastSyncAt ? timeAgo(outbox.lastSyncAt) : "Not yet"} good={outbox.items.length === 0} detail={outbox.items.length ? `${outbox.items.length} waiting` : undefined} />
        </Card>

        <Button
          testID="sign-out"
          variant="dangerSoft"
          icon="log-out-outline"
          label="Sign out"
          large
          onPress={() => void signOut({ code: "SIGNED_OUT", message: "Signed out." })}
        />
        <Text style={styles.footer}>Guard Portal · secure access</Text>
      </ScrollView>
    </View>
  );
}

function Field({ label, value, locked = false }: { label: string; value: string; locked?: boolean }) {
  return (
    <View style={styles.field}>
      <View style={{ flex: 1 }}>
        <Text style={styles.fieldLabel}>{label}</Text>
        <Text style={styles.fieldValue}>{value}</Text>
      </View>
      {locked ? <Ionicons name="lock-closed-outline" size={16} color={colors.textSubtle} accessibilityLabel="Read-only" /> : null}
    </View>
  );
}

function Diag({ label, value, good, detail }: { label: string; value: string; good: boolean; detail?: string }) {
  return (
    <View style={styles.diag}>
      <View style={[styles.diagDot, { backgroundColor: good ? colors.success : colors.warning }]} />
      <View style={{ flex: 1 }}>
        <Text style={styles.fieldLabel}>{label}</Text>
        <Text style={styles.fieldValue}>{value}</Text>
        {detail ? <Text style={styles.muted}>{detail}</Text> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16, gap: 14, paddingBottom: 120 },
  identity: { flexDirection: "row", gap: 14, alignItems: "center" },
  avatar: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.primaryTint,
    alignItems: "center",
    justifyContent: "center",
  },
  name: { fontSize: 20, fontWeight: "800", color: colors.text },
  muted: { fontSize: 13, color: colors.textMuted },
  badges: { flexDirection: "row", gap: 6, marginTop: 6 },
  field: { flexDirection: "row", alignItems: "center", borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  fieldLabel: { fontSize: 12, fontWeight: "700", color: colors.textSubtle },
  fieldValue: { fontSize: 15, color: colors.text },
  notice: { flexDirection: "row", gap: 8, backgroundColor: colors.background, borderRadius: radius.md, padding: 12 },
  noticeText: { flex: 1, fontSize: 12, color: colors.textMuted, lineHeight: 17 },
  section: { fontSize: 16, fontWeight: "800", color: colors.text },
  diag: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  diagDot: { width: 10, height: 10, borderRadius: 5, marginTop: 5 },
  footer: { textAlign: "center", fontSize: 12, color: colors.textSubtle },
});
