/**
 * Screen 7 — Incident & Alert Group Chat. One thread component for the alert chat (/alerts/{id}/messages) and the
 * case chat (/incidents/{id}/messages): sender name and role, Admin / Supervisor messages shown as directives,
 * timestamps, delivery state, a voice-call button, real-time refresh (WebSocket) with polling as the fallback. The
 * server decides who may read and post (participants only); a 404 means this Guard is not part of the chat.
 */
import { Ionicons } from "@expo/vector-icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { FlatList, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { ApiError, errorMessage } from "../../api/client";
import { alertsApi, guardApi, incidentsApi } from "../../api/endpoints";
import type { StaffRole } from "../../api/types";
import { ActiveCallNotice, CallButton } from "../../calls/CallViews";
import { Badge } from "../../components/Badge";
import { Banner } from "../../components/Banner";
import { Header } from "../../components/Header";
import { ErrorState, Loading } from "../../components/States";
import { ROLE_LABEL, formatDateTime, formatTime } from "../../lib/format";
import { newId } from "../../lib/ids";
import { useOnline } from "../../lib/network";
import { qk } from "../../lib/queryKeys";
import { useRealtime } from "../../realtime/RealtimeProvider";
import { colors, radius } from "../../theme";

export interface ChatMessage {
  id: string;
  sender_name: string;
  sender_role: StaffRole;
  body: string;
  created_at: string;
}

interface LocalMessage {
  localId: string;
  body: string;
  state: "sending" | "failed";
  error?: string;
  clientMessageId: string;
}

export interface ChatThreadProps {
  scope: "ALERT" | "INCIDENT";
  id: string;
  title: string;
  subtitle: string;
  context?: string;
  closed: boolean;
}

export function ChatThread({ scope, id, title, subtitle, context, closed }: ChatThreadProps) {
  const queryClient = useQueryClient();
  const online = useOnline();
  const { pollMs, live } = useRealtime();
  const [draft, setDraft] = useState("");
  const [local, setLocal] = useState<LocalMessage[]>([]);
  const listRef = useRef<FlatList<ChatMessage | LocalMessage>>(null);
  const key = scope === "ALERT" ? qk.alertMessages(id) : qk.incidentMessages(id);
  const dashboard = useQuery({ queryKey: qk.dashboard, queryFn: guardApi.dashboard });
  const myName = dashboard.data?.guard.full_name ?? null;
  const messages = useQuery<ChatMessage[]>({
    queryKey: key,
    queryFn: () => (scope === "ALERT" ? alertsApi.messages(id) : incidentsApi.messages(id)),
    refetchInterval: pollMs,
  });

  useEffect(() => {
    const timer = setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    return () => clearTimeout(timer);
  }, [messages.data?.length, local.length]);

  const send = async (message: LocalMessage) => {
    setLocal((list) => list.map((m) => (m.localId === message.localId ? { ...m, state: "sending", error: undefined } : m)));
    try {
      if (scope === "ALERT") await alertsApi.postMessage(id, message.body, message.clientMessageId);
      else await incidentsApi.postMessage(id, message.body);
      setLocal((list) => list.filter((m) => m.localId !== message.localId));
      await queryClient.invalidateQueries({ queryKey: key });
    } catch (error) {
      const text =
        error instanceof ApiError && error.isNetworkError
          ? "Not sent: no connection. Tap to retry."
          : `Not sent: ${errorMessage(error)}`;
      setLocal((list) => list.map((m) => (m.localId === message.localId ? { ...m, state: "failed", error: text } : m)));
    }
  };

  const submit = () => {
    const body = draft.trim();
    if (!body) return;
    const message: LocalMessage = { localId: newId(), body, state: "sending", clientMessageId: newId() };
    setDraft("");
    setLocal((list) => [...list, message]);
    void send(message);
  };

  const notMember = messages.error instanceof ApiError && messages.error.status === 404;
  const items: (ChatMessage | LocalMessage)[] = [...(messages.data ?? []), ...local];

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <Header title={title} subtitle={subtitle} back right={closed ? null : <CallButton target={{ scope, id }} compact />}>
        <View style={styles.headerRow}>
          <Badge label="Participants only" tone="primary" />
          <Text style={styles.headerMeta}>{live ? "Live" : online ? "Updating every few seconds" : "Offline"}</Text>
        </View>
        {context ? <Text style={styles.headerMeta}>{context}</Text> : null}
      </Header>
      <View style={styles.pad}>
        <ActiveCallNotice target={{ scope, id }} />
      </View>
      {messages.isPending ? (
        <Loading label="Loading messages…" />
      ) : notMember ? (
        <ErrorState error={new Error("You are not a participant of this chat.")} />
      ) : messages.isError && !messages.data ? (
        <ErrorState error={messages.error} onRetry={() => void messages.refetch()} />
      ) : (
        <FlatList
          ref={listRef}
          data={items}
          keyExtractor={(m) => ("id" in m ? m.id : m.localId)}
          contentContainerStyle={styles.list}
          ListEmptyComponent={<Text style={styles.empty}>No messages yet. Share updates, arrival status or needs here.</Text>}
          renderItem={({ item }) =>
            "id" in item ? (
              <Bubble message={item} mine={item.sender_role === "GUARD" && item.sender_name === myName} />
            ) : (
              <Pending message={item} onRetry={() => void send(item)} />
            )
          }
        />
      )}
      {closed ? (
        <View style={styles.pad}>
          <Banner tone="info" message="This chat is closed: the alert or case was resolved or dismissed." />
        </View>
      ) : (
        <View style={styles.composer}>
          <TextInput
            testID="chat-input"
            accessibilityLabel="Message"
            style={styles.input}
            value={draft}
            onChangeText={setDraft}
            placeholder="Message the response team…"
            placeholderTextColor={colors.textSubtle}
            multiline
            maxLength={2000}
          />
          <Pressable
            testID="chat-send"
            accessibilityRole="button"
            accessibilityLabel="Send message"
            disabled={!draft.trim()}
            onPress={submit}
            style={[styles.send, !draft.trim() && { opacity: 0.5 }]}
          >
            <Ionicons name="send" size={20} color={colors.onPrimary} />
          </Pressable>
        </View>
      )}
    </KeyboardAvoidingView>
  );
}

function Bubble({ message, mine }: { message: ChatMessage; mine: boolean }) {
  const directive = message.sender_role === "ADMIN" || message.sender_role === "SUPERVISOR";
  if (directive) {
    return (
      <View style={styles.directive} testID="directive-message">
        <Text style={styles.directiveLabel}>{ROLE_LABEL[message.sender_role].toUpperCase()} DIRECTIVE</Text>
        <Text style={styles.directiveText}>{message.body}</Text>
        <Text style={styles.directiveMeta}>
          {message.sender_name} · {formatDateTime(message.created_at)}
        </Text>
      </View>
    );
  }
  return (
    <View style={[styles.bubbleWrap, mine && styles.mineWrap]}>
      <Text style={styles.sender}>
        {mine ? "You" : `${message.sender_name} · ${ROLE_LABEL[message.sender_role]}`}
      </Text>
      <View style={[styles.bubble, mine && styles.mine]}>
        <Text style={[styles.body, mine && styles.mineBody]}>{message.body}</Text>
        <Text style={[styles.time, mine && styles.mineTime]}>{formatTime(message.created_at)} ✓</Text>
      </View>
    </View>
  );
}

function Pending({ message, onRetry }: { message: LocalMessage; onRetry: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={message.state === "sending"}
      onPress={onRetry}
      style={[styles.bubbleWrap, styles.mineWrap]}
    >
      <View style={[styles.bubble, styles.mine, message.state === "failed" && styles.failed]}>
        <Text style={[styles.body, styles.mineBody]}>{message.body}</Text>
        <Text style={[styles.time, styles.mineTime]}>{message.state === "sending" ? "Sending…" : message.error}</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  pad: { paddingHorizontal: 16, paddingTop: 8 },
  headerRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  headerMeta: { color: colors.textOnNavyMuted, fontSize: 12 },
  list: { padding: 16, gap: 12 },
  empty: { textAlign: "center", color: colors.textMuted, marginTop: 24 },
  directive: {
    backgroundColor: colors.infoTint,
    borderColor: colors.primary,
    borderWidth: 1.5,
    borderRadius: radius.lg,
    padding: 14,
    gap: 6,
  },
  directiveLabel: { fontSize: 11, fontWeight: "800", color: colors.primary, letterSpacing: 0.8 },
  directiveText: { fontSize: 15, fontWeight: "700", color: colors.info },
  directiveMeta: { fontSize: 12, color: colors.textMuted },
  bubbleWrap: { alignItems: "flex-start", gap: 4, maxWidth: "85%" },
  mineWrap: { alignSelf: "flex-end", alignItems: "flex-end" },
  sender: { fontSize: 12, fontWeight: "700", color: colors.textMuted },
  bubble: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 12,
    gap: 6,
  },
  mine: { backgroundColor: colors.primary, borderColor: colors.primary },
  failed: { backgroundColor: colors.danger, borderColor: colors.danger },
  body: { fontSize: 15, color: colors.text },
  mineBody: { color: colors.onPrimary },
  time: { fontSize: 11, color: colors.textSubtle, alignSelf: "flex-end" },
  mineTime: { color: colors.primaryBorder },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 10,
    padding: 12,
    paddingBottom: 20,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  input: {
    flex: 1,
    minHeight: 48,
    maxHeight: 120,
    backgroundColor: colors.background,
    borderRadius: radius.xl,
    paddingHorizontal: 16,
    paddingVertical: 12,
    fontSize: 15,
    color: colors.text,
  },
  send: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
});
