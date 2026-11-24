/**
 * Screen 5 — Assigned Alerts / Home, as the Standby Emergency Dashboard or the Stationed Monitoring Dashboard
 * (decision D-018). The dashboard follows GET /guard/dashboard (the current assignment); the Guard cannot change it.
 */
import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";

import { guardApi } from "../../api/endpoints";
import type { AlertResponse } from "../../api/types";
import { useSignedIn } from "../../auth/SessionProvider";
import { Badge, PriorityBadge, StatusBadge } from "../../components/Badge";
import { Banner } from "../../components/Banner";
import { Header } from "../../components/Header";
import { Empty, ErrorState, Loading } from "../../components/States";
import {
  ALERT_TYPE_LABEL,
  REASON_LABEL,
  alertLocationText,
  byUrgency,
  isClosed,
  myRecipient,
  ownLocationText,
  timeAgo,
} from "../../lib/format";
import { useOnline } from "../../lib/network";
import { qk } from "../../lib/queryKeys";
import { useRealtime } from "../../realtime/RealtimeProvider";
import { useUnreadCount } from "../notifications/NotificationsScreen";
import { colors, radius } from "../../theme";
import { useAlertsQuery } from "./queries";

export type AlertTab = "ACTIVE" | "RESPONDING" | "HISTORY";

export function alertsForTab(alerts: AlertResponse[], tab: AlertTab): AlertResponse[] {
  const list = alerts.filter((a) => {
    const closed = isClosed(a.status);
    const responding = myRecipient(a)?.state === "RESPONDING";
    if (tab === "HISTORY") return closed;
    if (tab === "RESPONDING") return !closed && responding;
    return !closed && !responding;
  });
  return tab === "HISTORY"
    ? list.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    : list.sort(byUrgency);
}

const TABS: { key: AlertTab; label: string }[] = [
  { key: "ACTIVE", label: "Active / New" },
  { key: "RESPONDING", label: "My Responding" },
  { key: "HISTORY", label: "Resolved / History" },
];

export default function DashboardScreen() {
  const router = useRouter();
  const session = useSignedIn();
  const online = useOnline();
  const realtime = useRealtime();
  const [tab, setTab] = useState<AlertTab>("ACTIVE");
  const dashboard = useQuery({ queryKey: qk.dashboard, queryFn: guardApi.dashboard });
  const location = useQuery({ queryKey: qk.location, queryFn: guardApi.location });
  const alerts = useAlertsQuery();
  const unreadNotifications = useUnreadCount();

  const kind = dashboard.data?.dashboard ?? session.dashboard;
  const standby = kind === "STANDBY_EMERGENCY";
  const visible = useMemo(() => alertsForTab(alerts.data ?? [], tab), [alerts.data, tab]);
  const unacknowledged = (alerts.data ?? []).filter((a) => !isClosed(a.status) && myRecipient(a)?.state === "NOTIFIED").length;

  const refresh = () => {
    void alerts.refetch();
    void dashboard.refetch();
    void location.refetch();
  };

  const pill = online ? (
    <View style={styles.online} testID="online-pill">
      <View style={styles.dot} />
      <Text style={styles.onlineText}>{realtime.live ? "Live" : "Online"}</Text>
    </View>
  ) : (
    <View style={[styles.online, styles.offline]} testID="offline-pill">
      <Text style={styles.offlineText}>Offline</Text>
    </View>
  );

  return (
    <View style={styles.flex}>
      <Header
        title={standby ? "Standby Emergency Dashboard" : "Stationed Monitoring Dashboard"}
        subtitle={dashboard.data ? `${dashboard.data.organization.name} · ${dashboard.data.campus.name}` : undefined}
        right={
          <View style={styles.headerRight}>
            {pill}
            <Pressable
              testID="open-notifications"
              accessibilityRole="button"
              accessibilityLabel={`Notifications, ${unreadNotifications} unread`}
              onPress={() => router.push("/notifications")}
              style={styles.bell}
            >
              <Ionicons name="notifications-outline" size={22} color={colors.textOnNavy} />
              {unreadNotifications > 0 ? (
                <View style={styles.bellBadge}>
                  <Text style={styles.bellText}>{unreadNotifications > 9 ? "9+" : unreadNotifications}</Text>
                </View>
              ) : null}
            </Pressable>
          </View>
        }
      >
        <View style={styles.context} testID="dashboard-context">
          <Ionicons name={standby ? "flash" : "location"} size={16} color={colors.textOnNavyMuted} />
          <Text style={styles.contextText}>
            {standby
              ? "Standby / Quick Response: campus-wide escalations, assistance requests and rapid response."
              : `Your post: ${ownLocationText(location.data)}`}
          </Text>
        </View>
      </Header>
      {!online ? (
        <View style={styles.pad}>
          <Banner
            tone="warning"
            title="Offline mode"
            message="Real-time server alerts are paused while you are offline. Manual reports are saved and sent later."
            testID="offline-banner"
          />
        </View>
      ) : null}
      <View style={styles.tabs} accessibilityRole="tablist">
        {TABS.map((t) => (
          <Pressable
            key={t.key}
            testID={`tab-${t.key}`}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === t.key }}
            onPress={() => setTab(t.key)}
            style={[styles.tab, tab === t.key && styles.tabActive]}
          >
            <Text style={[styles.tabText, tab === t.key && styles.tabTextActive]}>
              {t.label}
              {t.key === "ACTIVE" && unacknowledged > 0 ? ` (${unacknowledged})` : ""}
            </Text>
          </Pressable>
        ))}
      </View>
      <Text style={styles.sync}>
        {realtime.live ? "Live updates on" : `Checking for alerts every ${Math.round(realtime.pollMs / 1000)} s`} · pull
        down to refresh
      </Text>
      {alerts.isPending ? (
        <Loading label="Loading your alerts…" />
      ) : alerts.isError && !alerts.data ? (
        <ErrorState error={alerts.error} onRetry={refresh} />
      ) : (
        <FlatList
          data={visible}
          keyExtractor={(a) => a.id}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={alerts.isRefetching} onRefresh={refresh} />}
          renderItem={({ item }) => <AlertCard alert={item} onOpen={() => router.push(`/alert/${item.id}`)} />}
          ListEmptyComponent={
            <Empty
              title={tab === "HISTORY" ? "No closed alerts yet" : "No alerts right now"}
              message={
                tab === "HISTORY"
                  ? undefined
                  : standby
                    ? "As a Standby Guard you receive alerts when they are escalated to you or a Guard asks for assistance."
                    : "Alerts from cameras at your post appear here, together with escalations sent to you."
              }
            />
          }
        />
      )}
    </View>
  );
}

export function AlertCard({ alert, onOpen }: { alert: AlertResponse; onOpen: () => void }) {
  const mine = myRecipient(alert);
  const unacknowledged = !isClosed(alert.status) && mine?.state === "NOTIFIED";
  return (
    <Pressable
      testID={`alert-card-${alert.id}`}
      accessibilityRole="button"
      accessibilityLabel={`${ALERT_TYPE_LABEL[alert.alert_type]} at ${alert.camera_name}`}
      onPress={onOpen}
      style={[styles.card, unacknowledged && styles.cardUrgent]}
    >
      <View style={styles.cardTop}>
        <PriorityBadge priority={alert.priority} />
        <Text style={styles.ref}>#{alert.id.slice(0, 8).toUpperCase()}</Text>
      </View>
      <Text style={styles.cardTitle}>{ALERT_TYPE_LABEL[alert.alert_type]}</Text>
      <View style={styles.meta}>
        <Ionicons name="videocam-outline" size={15} color={colors.textMuted} />
        <Text style={styles.metaText} numberOfLines={2}>
          {alert.camera_name} · {alertLocationText(alert.location)}
        </Text>
      </View>
      {mine && mine.reason !== "LOCATION" ? <Badge label={REASON_LABEL[mine.reason]} tone="info" /> : null}
      <View style={styles.cardBottom}>
        <Text style={styles.time}>{timeAgo(alert.last_detected_at)}</Text>
        <StatusBadge status={alert.status} />
        {unacknowledged ? <Badge label="Not acknowledged" tone="danger" /> : null}
        <Text style={styles.open}>View details ›</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  pad: { paddingHorizontal: 16, paddingTop: 12 },
  context: { flexDirection: "row", gap: 8, alignItems: "flex-start" },
  headerRight: { alignItems: "flex-end", gap: 8 },
  bell: {
    width: 44,
    height: 44,
    borderRadius: radius.md,
    backgroundColor: colors.navyRaised,
    alignItems: "center",
    justifyContent: "center",
  },
  bellBadge: {
    position: "absolute",
    top: 4,
    right: 4,
    minWidth: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: colors.dangerStrong,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  bellText: { color: colors.onPrimary, fontSize: 10, fontWeight: "800" },
  contextText: { flex: 1, color: colors.textOnNavyMuted, fontSize: 13, lineHeight: 18 },
  online: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.successPillOnNavy,
    borderRadius: radius.pill,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.successOnNavy },
  onlineText: { color: colors.successOnNavy, fontWeight: "700", fontSize: 12 },
  offline: { backgroundColor: colors.warningTint },
  offlineText: { color: colors.warning, fontWeight: "700", fontSize: 12 },
  tabs: {
    flexDirection: "row",
    margin: 16,
    marginBottom: 6,
    backgroundColor: colors.border,
    borderRadius: radius.md,
    padding: 4,
  },
  tab: { flex: 1, paddingVertical: 10, borderRadius: radius.sm, alignItems: "center" },
  tabActive: { backgroundColor: colors.primary },
  tabText: { fontSize: 12, fontWeight: "700", color: colors.textMuted, textAlign: "center" },
  tabTextActive: { color: colors.onPrimary },
  sync: { textAlign: "center", fontSize: 12, color: colors.textSubtle, marginBottom: 6 },
  list: { padding: 16, gap: 12, paddingBottom: 120 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 16,
    gap: 8,
  },
  cardUrgent: { borderColor: colors.danger, borderWidth: 2 },
  cardTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  ref: { fontSize: 12, color: colors.textSubtle, fontWeight: "600" },
  cardTitle: { fontSize: 18, fontWeight: "800", color: colors.text },
  meta: { flexDirection: "row", gap: 6, alignItems: "flex-start" },
  metaText: { flex: 1, fontSize: 13, color: colors.textMuted },
  cardBottom: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap", borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  time: { fontSize: 12, color: colors.textMuted },
  open: { marginLeft: "auto", color: colors.primary, fontWeight: "700", fontSize: 13 },
});
