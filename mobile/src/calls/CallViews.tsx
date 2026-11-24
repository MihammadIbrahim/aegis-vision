import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { callsApi, type CallTargetRef } from "../api/endpoints";
import type { CallSessionResponse } from "../api/types";
import { Badge } from "../components/Badge";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { PARTICIPANT_LABEL, ROLE_LABEL, formatDuration } from "../lib/format";
import { qk } from "../lib/queryKeys";
import { useRealtime } from "../realtime/RealtimeProvider";
import { colors, radius } from "../theme";
import { useCalls } from "./CallProvider";

function targetLabel(call: CallSessionResponse): string {
  return call.scope === "ALERT" ? "Alert call" : "Case call";
}

/** A ringing invitation (shown above the screens; the alarm, if any, stays on top of it). */
export function IncomingCallCard() {
  const calls = useCalls();
  const insets = useSafeAreaInsets();
  const call = calls.incoming;
  if (!call) return null;
  const starter = call.started_by_name ?? "Someone";
  return (
    <View testID="incoming-call" style={[styles.incoming, { top: insets.top + 8 }]} accessibilityRole="alert">
      <View style={styles.incomingIcon}>
        <Ionicons name="call" size={22} color={colors.onPrimary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.incomingTitle}>Incoming {targetLabel(call).toLowerCase()}</Text>
        <Text style={styles.incomingText}>
          {starter} ({ROLE_LABEL[call.started_by_role]}) · {call.joined_count} in the call
        </Text>
        {call.target_title ? (
          <Text style={styles.incomingText} numberOfLines={2}>
            {call.target_title}
            {call.target_location ? ` · ${call.target_location}` : ""}
          </Text>
        ) : null}
      </View>
      <Pressable
        testID="incoming-decline"
        accessibilityRole="button"
        accessibilityLabel="Decline call"
        onPress={() => void calls.decline(call)}
        style={[styles.round, { backgroundColor: colors.danger }]}
      >
        <Ionicons name="close" size={22} color={colors.onPrimary} />
      </Pressable>
      <Pressable
        testID="incoming-accept"
        accessibilityRole="button"
        accessibilityLabel="Answer call"
        onPress={() => void calls.accept(call)}
        style={[styles.round, { backgroundColor: colors.success }]}
      >
        <Ionicons name="call" size={20} color={colors.onPrimary} />
      </Pressable>
    </View>
  );
}

function useTicker(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/** The active call: full screen, or a small bar when minimized. */
export function ActiveCallOverlay() {
  const calls = useCalls();
  const insets = useSafeAreaInsets();
  const now = useTicker();
  const current = calls.current;
  if (!current) return null;
  const duration = formatDuration((now - current.joinedAt) / 1000);
  const phaseText =
    current.phase === "connecting" ? "Connecting…" : current.phase === "reconnecting" ? "Reconnecting…" : duration;

  if (!calls.expanded) {
    return (
      <Pressable
        testID="call-pill"
        accessibilityRole="button"
        accessibilityLabel="Return to the call"
        onPress={() => calls.setExpanded(true)}
        style={[styles.pill, { bottom: insets.bottom + 72 }]}
      >
        <Ionicons name="call" size={18} color={colors.onPrimary} />
        <Text style={styles.pillText}>
          {targetLabel(current.call)} · {phaseText}
        </Text>
      </Pressable>
    );
  }

  const people = current.call.participants.filter((p) => p.state === "JOINED" || p.state === "INVITED");
  return (
    <View testID="active-call" style={[styles.callScreen, { paddingTop: insets.top + 16, paddingBottom: insets.bottom + 20 }]}>
      <View style={styles.callTop}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Minimize call"
          onPress={() => calls.setExpanded(false)}
          style={styles.minimize}
        >
          <Ionicons name="chevron-down" size={24} color={colors.textOnNavy} />
        </Pressable>
        <Text style={styles.callTitle}>{targetLabel(current.call)}</Text>
        {current.call.target_title ? (
          <Text style={styles.callMeta} numberOfLines={2}>
            {current.call.target_title}
            {current.call.target_location ? ` · ${current.call.target_location}` : ""}
          </Text>
        ) : null}
        <Text testID="call-timer" style={styles.callTimer}>
          {phaseText}
        </Text>
        <Text style={styles.callMeta}>{"Audio only · only the people of this alert or case can join"}</Text>
      </View>
      <ScrollView contentContainerStyle={styles.participants}>
        {people.map((p) => {
          const live = p.is_me ? current.phase === "active" : current.connected.includes(p.user_id);
          return (
            <View key={p.user_id} style={styles.participant}>
              <Ionicons
                name={p.role === "GUARD" ? "shield-checkmark" : "person-circle"}
                size={26}
                color={colors.textOnNavy}
              />
              <View style={{ flex: 1 }}>
                <Text style={styles.participantName}>
                  {p.full_name}
                  {p.is_me ? " (you)" : ""}
                </Text>
                <Text style={styles.participantState}>{live ? "Connected" : PARTICIPANT_LABEL[p.state]}</Text>
              </View>
              <Badge label={ROLE_LABEL[p.role]} tone={p.role === "GUARD" ? "primary" : "info"} />
            </View>
          );
        })}
      </ScrollView>
      {calls.notice ? <Banner tone="warning" message={calls.notice} /> : null}
      <View style={styles.controls}>
        <Control
          testID="call-mute"
          icon={current.muted ? "mic-off" : "mic"}
          label={current.muted ? "Unmute" : "Mute"}
          active={current.muted}
          onPress={() => void calls.toggleMute()}
        />
        <Control
          testID="call-speaker"
          icon={current.speaker ? "volume-high" : "volume-low"}
          label={current.speaker ? "Speaker on" : "Speaker"}
          active={current.speaker}
          onPress={() => void calls.toggleSpeaker()}
        />
        <Control testID="call-leave" icon="call" label="Leave" danger onPress={() => void calls.leave()} />
      </View>
      {current.call.can_end ? (
        <Button
          testID="call-end-all"
          variant="dangerSoft"
          label="End call for everyone"
          disabled={calls.busy}
          onPress={() => void calls.endForEveryone()}
        />
      ) : null}
    </View>
  );
}

function Control({
  icon,
  label,
  onPress,
  active = false,
  danger = false,
  testID,
}: {
  icon: "mic" | "mic-off" | "volume-high" | "volume-low" | "call";
  label: string;
  onPress: () => void;
  active?: boolean;
  danger?: boolean;
  testID?: string;
}) {
  return (
    <View style={styles.control}>
      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityState={{ selected: active }}
        onPress={onPress}
        style={[
          styles.controlButton,
          active && { backgroundColor: colors.onPrimary },
          danger && { backgroundColor: colors.danger, transform: [{ rotate: "135deg" }] },
        ]}
      >
        <Ionicons name={icon} size={26} color={active ? colors.navy : colors.onPrimary} />
      </Pressable>
      <Text style={styles.controlLabel}>{label}</Text>
    </View>
  );
}

/** "In-App Call" for an alert or a case; shows whether a call is already going on. */
export function CallButton({ target, compact = false }: { target: CallTargetRef; compact?: boolean }) {
  const calls = useCalls();
  const { pollMs } = useRealtime();
  const active = useQuery({
    queryKey: qk.activeCall(target.scope, target.id),
    queryFn: () => callsApi.active(target),
    refetchInterval: pollMs,
  });
  const ongoing = active.data?.call ?? null;
  const inThisCall = calls.current !== null && ongoing !== null && calls.current.call.id === ongoing.id;
  const label = inThisCall ? "Open call" : ongoing ? `Join call (${ongoing.joined_count})` : "In-App Call";
  const onPress = () => {
    if (inThisCall) calls.setExpanded(true);
    else if (ongoing) void calls.accept(ongoing);
    else void calls.start(target);
  };
  if (compact) {
    return (
      <Pressable
        testID="call-button"
        accessibilityRole="button"
        accessibilityLabel={label}
        onPress={onPress}
        style={styles.headerCall}
      >
        <Ionicons name="call" size={20} color={colors.onPrimary} />
        {ongoing ? <View style={styles.liveDot} /> : null}
      </Pressable>
    );
  }
  return (
    <View style={{ gap: 6 }}>
      <Button testID="call-button" variant="outline" icon="call" label={label} loading={calls.busy} onPress={onPress} />
      {active.data && !active.data.voice_calls_available ? (
        <Text style={styles.callHint}>Voice calls are not configured on the server yet.</Text>
      ) : !calls.voiceAvailable ? (
        <Text style={styles.callHint}>{calls.voiceUnavailableReason}</Text>
      ) : null}
    </View>
  );
}

/** Shown on a chat or alert when someone is already in a call about it. */
export function ActiveCallNotice({ target }: { target: CallTargetRef }) {
  const { pollMs } = useRealtime();
  const active = useQuery({
    queryKey: qk.activeCall(target.scope, target.id),
    queryFn: () => callsApi.active(target),
    refetchInterval: pollMs,
  });
  const call = active.data?.call;
  if (!call || call.joined_count === 0) return null;
  const names = call.participants.filter((p) => p.state === "JOINED").map((p) => `${p.full_name} (${ROLE_LABEL[p.role]})`);
  return <Banner tone="success" title="A call is going on" message={names.join(", ")} testID="active-call-notice" />;
}

const styles = StyleSheet.create({
  incoming: {
    position: "absolute",
    left: 12,
    right: 12,
    zIndex: 900,
    elevation: 16,
    backgroundColor: colors.navy,
    borderRadius: radius.lg,
    padding: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  incomingIcon: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  incomingTitle: { color: colors.textOnNavy, fontWeight: "700", fontSize: 15 },
  incomingText: { color: colors.textOnNavyMuted, fontSize: 12 },
  round: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  pill: {
    position: "absolute",
    alignSelf: "center",
    zIndex: 800,
    elevation: 12,
    backgroundColor: colors.success,
    borderRadius: radius.pill,
    paddingHorizontal: 16,
    paddingVertical: 10,
    flexDirection: "row",
    gap: 8,
    alignItems: "center",
  },
  pillText: { color: colors.onPrimary, fontWeight: "700" },
  callScreen: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 800,
    elevation: 12,
    backgroundColor: colors.navy,
    paddingHorizontal: 20,
    gap: 16,
  },
  callTop: { alignItems: "center", gap: 4 },
  minimize: { alignSelf: "flex-start", padding: 6 },
  callTitle: { color: colors.textOnNavy, fontSize: 22, fontWeight: "800" },
  callTimer: { color: colors.successOnNavy, fontSize: 18, fontWeight: "700" },
  callMeta: { color: colors.textOnNavyMuted, fontSize: 12 },
  participants: { gap: 10 },
  participant: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.navyRaised,
    borderRadius: radius.md,
    padding: 12,
  },
  participantName: { color: colors.textOnNavy, fontSize: 15, fontWeight: "600" },
  participantState: { color: colors.textOnNavyMuted, fontSize: 12 },
  controls: { flexDirection: "row", justifyContent: "space-around" },
  control: { alignItems: "center", gap: 6 },
  controlButton: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.navyBorder,
    alignItems: "center",
    justifyContent: "center",
  },
  controlLabel: { color: colors.textOnNavy, fontSize: 12 },
  headerCall: {
    width: 44,
    height: 44,
    borderRadius: radius.md,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  liveDot: {
    position: "absolute",
    top: 6,
    right: 6,
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: colors.successOnNavy,
  },
  callHint: { fontSize: 12, color: colors.textMuted },
});
