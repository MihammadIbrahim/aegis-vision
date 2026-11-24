/**
 * The Guard's in-app notifications (GET /notifications): new and escalated alerts, assistance broadcasts, case-chat
 * invitations and account notices such as a new Guard ID. Unread ones are marked; opening one marks it read and goes
 * to the alert or case chat it is about.
 */
import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";

import { guardApi } from "../../api/endpoints";
import type { NotificationResponse } from "../../api/types";
import { Button } from "../../components/Button";
import { Header } from "../../components/Header";
import { Empty, ErrorState, Loading } from "../../components/States";
import { formatDateTime, timeAgo } from "../../lib/format";
import { qk } from "../../lib/queryKeys";
import { useRealtime } from "../../realtime/RealtimeProvider";
import { colors, radius } from "../../theme";

export function targetOf(n: NotificationResponse): string | null {
  if (n.incident_id) return `/chat/incident/${n.incident_id}`;
  if (n.alert_id) return `/alert/${n.alert_id}`;
  return null;
}

export function useUnreadCount(): number {
  const { pollMs } = useRealtime();
  const unread = useQuery({
    queryKey: [...qk.notifications, "unread"],
    queryFn: () => guardApi.notifications(true),
    refetchInterval: Math.max(pollMs, 15_000),
  });
  return unread.data?.length ?? 0;
}

export default function NotificationsScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { pollMs } = useRealtime();
  const list = useQuery({ queryKey: qk.notifications, queryFn: () => guardApi.notifications(false), refetchInterval: pollMs });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: qk.notifications });
  const read = useMutation({ mutationFn: (id: string) => guardApi.readNotification(id), onSuccess: refresh });
  const readAll = useMutation({ mutationFn: guardApi.readAllNotifications, onSuccess: refresh });
  const unread = (list.data ?? []).filter((n) => !n.read_at).length;

  const open = (n: NotificationResponse) => {
    if (!n.read_at) read.mutate(n.id);
    const target = targetOf(n);
    if (target) router.push(target);
  };

  return (
    <View style={styles.flex}>
      <Header title="Notifications" subtitle={unread ? `${unread} unread` : "All read"} back />
      {list.isPending ? (
        <Loading />
      ) : list.isError && !list.data ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <FlatList
          data={list.data ?? []}
          keyExtractor={(n) => n.id}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={list.isRefetching} onRefresh={() => void list.refetch()} />}
          ListHeaderComponent={
            unread > 0 ? (
              <Button testID="read-all" variant="ghost" label="Mark all as read" loading={readAll.isPending} onPress={() => readAll.mutate()} />
            ) : undefined
          }
          ListEmptyComponent={<Empty title="No notifications" />}
          renderItem={({ item }) => (
            <Pressable
              testID={`notification-${item.id}`}
              accessibilityRole="button"
              accessibilityState={{ selected: !item.read_at }}
              onPress={() => open(item)}
              style={[styles.row, !item.read_at && styles.unread]}
            >
              <Ionicons
                name={item.incident_id ? "folder-open" : item.alert_id ? "notifications" : "person-circle"}
                size={22}
                color={item.read_at ? colors.textSubtle : colors.primary}
              />
              <View style={{ flex: 1 }}>
                <Text style={[styles.title, !item.read_at && styles.titleUnread]}>{item.title}</Text>
                <Text style={styles.meta}>
                  {formatDateTime(item.created_at)} · {timeAgo(item.created_at)}
                </Text>
              </View>
              {!item.read_at ? <View style={styles.dot} accessibilityLabel="Unread" /> : null}
            </Pressable>
          )}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  list: { padding: 16, gap: 10, paddingBottom: 60 },
  row: {
    flexDirection: "row",
    gap: 12,
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 14,
  },
  unread: { borderColor: colors.primaryBorder, backgroundColor: colors.primaryTint },
  title: { fontSize: 14, color: colors.text },
  titleUnread: { fontWeight: "700" },
  meta: { fontSize: 12, color: colors.textMuted },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.primary },
});
