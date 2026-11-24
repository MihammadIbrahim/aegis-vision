/**
 * Screen 9 — My Reports & Offline Queue. The outbox on this phone (Pending offline / Retrying / Failed) and the
 * reports the server received (Synced, with the review state). "Retry all" sends the outbox through POST /guard/sync;
 * resending is idempotent (client_report_id), so a report is never created twice.
 */
import { Ionicons } from "@expo/vector-icons";
import { useInfiniteQuery } from "@tanstack/react-query";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { REPORT_PAGE, reportsApi } from "../../api/endpoints";
import type { ManualReportResponse } from "../../api/types";
import { Badge, type Tone } from "../../components/Badge";
import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Header } from "../../components/Header";
import { Empty } from "../../components/States";
import { formatDateTime, timeAgo } from "../../lib/format";
import { useOnline } from "../../lib/network";
import { qk } from "../../lib/queryKeys";
import type { OutboxItem, OutboxState } from "../../offline/outbox";
import { useOutbox } from "../../offline/OutboxProvider";
import { colors, radius } from "../../theme";

export const SYNC_BADGE: Record<OutboxState | "SYNCED", { label: string; tone: Tone }> = {
  SYNCED: { label: "Synced", tone: "success" },
  PENDING: { label: "Pending offline", tone: "warning" },
  RETRYING: { label: "Retrying", tone: "primary" },
  FAILED: { label: "Failed", tone: "danger" },
};

function where(parts: (string | null)[]): string {
  return parts.filter(Boolean).join(", ");
}

export default function MyReportsScreen() {
  const online = useOnline();
  const outbox = useOutbox();
  const reports = useInfiniteQuery({
    queryKey: qk.reports,
    queryFn: ({ pageParam }) => reportsApi.mine(pageParam),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.length < REPORT_PAGE ? undefined : pages.length * REPORT_PAGE),
  });
  const history = reports.data?.pages.flat() ?? [];
  const waiting = outbox.items.length;

  return (
    <View style={styles.flex}>
      <Header title="My Reports & Offline Queue" subtitle="Your field reports, stored safely and synced" />
      <ScrollView
        contentContainerStyle={styles.body}
        refreshControl={<RefreshControl refreshing={reports.isRefetching} onRefresh={() => void reports.refetch()} />}
      >
        <Card testID="outbox-summary">
          <View style={styles.summary}>
            <View style={styles.icon}>
              <Ionicons name="cloud-upload-outline" size={24} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.summaryTitle}>
                {waiting === 0 ? "Nothing waiting to send" : `${waiting} report${waiting === 1 ? "" : "s"} in the offline outbox`}
              </Text>
              <Text style={styles.hint}>
                {outbox.lastSyncAt ? `Last sync ${timeAgo(outbox.lastSyncAt)}` : "Saved on this phone until the server confirms."}
              </Text>
            </View>
            <Button
              testID="retry-all"
              label="Retry all"
              loading={outbox.flushing}
              disabled={waiting === 0 || !online}
              onPress={() => void outbox.retryAll()}
            />
          </View>
          {!online ? <Text style={styles.hint}>Offline: sending resumes automatically when the connection returns.</Text> : null}
        </Card>
        {outbox.lastError && waiting > 0 ? <Banner tone="warning" message={outbox.lastError} /> : null}

        <Text style={styles.section}>Offline outbox ({waiting})</Text>
        {waiting === 0 ? <Empty title="The outbox is empty" /> : outbox.items.map((item) => <OutboxRow key={item.client_report_id} item={item} />)}

        <Text style={styles.section}>Submitted history ({history.length}{reports.hasNextPage ? "+" : ""})</Text>
        {history.length > 0 ? (
          history.map((report) => <ReportRow key={report.id} report={report} />)
        ) : (
          <Empty title={reports.isPending ? "Loading…" : "No reports submitted yet"} />
        )}
        {reports.hasNextPage ? (
          <Button
            testID="load-more-reports"
            variant="secondary"
            label="Load more"
            loading={reports.isFetchingNextPage}
            onPress={() => void reports.fetchNextPage()}
          />
        ) : null}
      </ScrollView>
    </View>
  );
}

function OutboxRow({ item }: { item: OutboxItem }) {
  const outbox = useOutbox();
  const badge = SYNC_BADGE[item.state];
  return (
    <View style={styles.row} testID={`outbox-${item.client_report_id}`}>
      <Text style={styles.title}>{item.report.report_type}</Text>
      <Text style={styles.meta}>
        {where([item.display.building_name, item.display.floor_name, item.display.area_name])} · saved{" "}
        {formatDateTime(item.queued_at)}
      </Text>
      <View style={styles.rowBottom}>
        <Badge label={badge.label} tone={badge.tone} testID={`state-${item.state}`} />
        <Text style={styles.hint}>
          {item.state === "PENDING"
            ? "Waiting for a connection"
            : item.state === "RETRYING"
              ? "Sending securely…"
              : (item.last_error ?? "Refused by the server")}
        </Text>
      </View>
      {item.state === "FAILED" ? (
        <Button variant="ghost" label="Remove from outbox" onPress={() => void outbox.discard(item.client_report_id)} />
      ) : null}
    </View>
  );
}

function ReportRow({ report }: { report: ManualReportResponse }) {
  return (
    <View style={styles.row} testID={`report-${report.id}`}>
      <Text style={styles.title}>{report.report_type}</Text>
      <Text style={styles.meta}>
        {where([report.building_name, report.floor_name, report.area_name])} · {formatDateTime(report.created_at)}
      </Text>
      <Text style={styles.description} numberOfLines={3}>
        {report.description}
      </Text>
      <View style={styles.rowBottom}>
        <Badge label={SYNC_BADGE.SYNCED.label} tone="success" />
        <Badge label={report.status === "REVIEWED" ? "Reviewed" : "Awaiting review"} tone={report.status === "REVIEWED" ? "info" : "neutral"} />
        {report.has_photo ? <Badge label="Photo" tone="neutral" /> : null}
      </View>
      {report.review_note ? <Text style={styles.hint}>Review note: {report.review_note}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  body: { padding: 16, gap: 12, paddingBottom: 120 },
  summary: { flexDirection: "row", alignItems: "center", gap: 12 },
  icon: {
    width: 48,
    height: 48,
    borderRadius: radius.md,
    backgroundColor: colors.primaryTint,
    alignItems: "center",
    justifyContent: "center",
  },
  summaryTitle: { fontSize: 15, fontWeight: "700", color: colors.text },
  hint: { fontSize: 12, color: colors.textMuted, flexShrink: 1 },
  section: { fontSize: 16, fontWeight: "800", color: colors.text, marginTop: 6 },
  row: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 14,
    gap: 6,
  },
  title: { fontSize: 17, fontWeight: "800", color: colors.text },
  meta: { fontSize: 13, color: colors.textMuted },
  description: { fontSize: 14, color: colors.text },
  rowBottom: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 4 },
});
