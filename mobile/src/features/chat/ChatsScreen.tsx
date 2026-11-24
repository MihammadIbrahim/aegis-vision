/** The Chats tab: the alert chats of alerts sent to this Guard and the case chats this Guard takes part in. */
import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { incidentsApi } from "../../api/endpoints";
import { Badge, PriorityBadge } from "../../components/Badge";
import { Header } from "../../components/Header";
import { Empty } from "../../components/States";
import { ALERT_TYPE_LABEL, INCIDENT_STATUS_LABEL, alertLocationText, byUrgency, isClosed, timeAgo } from "../../lib/format";
import { qk } from "../../lib/queryKeys";
import { useRealtime } from "../../realtime/RealtimeProvider";
import { colors, radius } from "../../theme";
import { useAlertsQuery } from "../alerts/queries";

export default function ChatsScreen() {
  const router = useRouter();
  const { pollMs } = useRealtime();
  const alerts = useAlertsQuery();
  const cases = useQuery({ queryKey: qk.incidents, queryFn: incidentsApi.mine, refetchInterval: pollMs });
  const openAlerts = (alerts.data ?? []).filter((a) => !isClosed(a.status)).sort(byUrgency);
  const refresh = () => {
    void alerts.refetch();
    void cases.refetch();
  };
  return (
    <View style={styles.flex}>
      <Header title="Group chats" subtitle="Alert and case coordination with Supervisors, Admins and Guards" />
      <ScrollView
        contentContainerStyle={styles.body}
        refreshControl={<RefreshControl refreshing={alerts.isRefetching || cases.isRefetching} onRefresh={refresh} />}
      >
        <Text style={styles.section}>Case chats</Text>
        {(cases.data ?? []).length === 0 ? (
          <Empty title="No case chats" message="You see a case here when you are invited to it or an alert sent to you is part of it." />
        ) : (
          (cases.data ?? []).map((c) => (
            <Pressable
              key={c.id}
              testID={`case-${c.id}`}
              accessibilityRole="button"
              onPress={() => router.push(`/chat/incident/${c.id}`)}
              style={styles.row}
            >
              <Ionicons name="folder-open" size={22} color={colors.primary} />
              <View style={{ flex: 1 }}>
                <Text style={styles.title}>
                  {c.reference} · {c.title}
                </Text>
                <Text style={styles.meta}>
                  {INCIDENT_STATUS_LABEL[c.status]} · updated {timeAgo(c.updated_at)}
                </Text>
              </View>
              {c.priority === "HIGH" ? <Badge label="HIGH" tone="danger" /> : null}
            </Pressable>
          ))
        )}
        <Text style={styles.section}>Alert chats</Text>
        {openAlerts.length === 0 ? (
          <Empty title="No open alerts" message="The chat of an alert sent to you appears here while it is open." />
        ) : (
          openAlerts.map((a) => (
            <Pressable
              key={a.id}
              testID={`alert-chat-${a.id}`}
              accessibilityRole="button"
              onPress={() => router.push(`/chat/alert/${a.id}`)}
              style={styles.row}
            >
              <Ionicons name="chatbubbles" size={22} color={colors.primary} />
              <View style={{ flex: 1 }}>
                <Text style={styles.title}>{ALERT_TYPE_LABEL[a.alert_type]}</Text>
                <Text style={styles.meta}>{alertLocationText(a.location)}</Text>
              </View>
              <PriorityBadge priority={a.priority} />
            </Pressable>
          ))
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16, gap: 10, paddingBottom: 120 },
  section: { fontSize: 13, fontWeight: "800", color: colors.textSubtle, letterSpacing: 0.6, marginTop: 6 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 14,
  },
  title: { fontSize: 15, fontWeight: "700", color: colors.text },
  meta: { fontSize: 12, color: colors.textMuted },
});
