/**
 * The offline outbox of manual reports (Guard app screens 8–9; requirements 7 and 15.6).
 *
 * A report that cannot be sent is kept on the phone with its client_report_id, created once, so resending it any
 * number of times creates one report on the server (idempotency). Photos are kept as files (documents/outbox/), not in
 * AsyncStorage, because AsyncStorage is small on Android. Items are tagged with the Guard ID that wrote them and are
 * only ever sent while that Guard is signed in.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Directory, File, Paths } from "expo-file-system";

import { ApiError } from "../api/client";
import type { ManualReportRequest, SyncItem, SyncItemResult } from "../api/types";
import { STORAGE_KEYS } from "../lib/storageKeys";

export type OutboxState = "PENDING" | "RETRYING" | "FAILED";

export interface OutboxItem {
  client_report_id: string;
  owner_guard_id: string;
  report: Omit<ManualReportRequest, "photo_base64">;
  photo_file: string | null;
  state: OutboxState;
  attempts: number;
  last_error: string | null;
  queued_at: string;
  display: { building_name: string; floor_name: string | null; area_name: string | null };
}

export interface OutboxStore {
  load(): Promise<OutboxItem[]>;
  save(items: OutboxItem[]): Promise<void>;
  writePhoto(id: string, base64: string): Promise<string>;
  readPhoto(uri: string): Promise<string>;
  deletePhoto(uri: string): Promise<void>;
}

export const deviceOutboxStore: OutboxStore = {
  async load() {
    const raw = await AsyncStorage.getItem(STORAGE_KEYS.outbox);
    if (!raw) return [];
    try {
      const items = JSON.parse(raw) as OutboxItem[];
      // An app closed while sending leaves RETRYING items behind: they are simply pending again.
      return items.map((i) => (i.state === "RETRYING" ? { ...i, state: "PENDING" } : i));
    } catch {
      return [];
    }
  },
  async save(items) {
    await AsyncStorage.setItem(STORAGE_KEYS.outbox, JSON.stringify(items));
  },
  async writePhoto(id, base64) {
    const folder = new Directory(Paths.document, "outbox");
    if (!folder.exists) folder.create({ intermediates: true });
    const file = new File(folder, `${id}.photo`);
    await file.write(base64, { encoding: "base64" });
    return file.uri;
  },
  async readPhoto(uri) {
    return new File(uri).base64();
  },
  async deletePhoto(uri) {
    const file = new File(uri);
    if (file.exists) file.delete();
  },
};

/** At most this much photo data (base64 characters) per sync request, and at most 50 items (server limit). */
const MAX_BATCH_CHARS = 9_000_000;
const MAX_BATCH_ITEMS = 50;

/** Retry later: the network, the server being busy or down. Anything else will fail the same way again. */
function isTemporary(status: number): boolean {
  return status === 0 || status === 401 || status === 403 || status === 408 || status === 429 || status >= 500;
}

export interface FlushResult {
  items: OutboxItem[];
  synced: number;
  failed: number;
}

/**
 * Sends the owner's pending items through POST /guard/sync, in order and in batches. Items reported OK (CREATED or
 * REPLAYED) leave the outbox; refused items become FAILED with the server's reason; temporary problems leave them
 * PENDING for the next attempt.
 */
export async function flushOutbox(
  all: OutboxItem[],
  owner: string,
  store: OutboxStore,
  sync: (items: SyncItem[]) => Promise<SyncItemResult[]>,
  includeFailed = false,
): Promise<FlushResult> {
  let items = all.slice();
  let synced = 0;
  let failed = 0;
  const due = items.filter(
    (i) => i.owner_guard_id === owner && (i.state === "PENDING" || (includeFailed && i.state === "FAILED")),
  );
  let index = 0;
  while (index < due.length) {
    const batch: OutboxItem[] = [];
    const payload: SyncItem[] = [];
    let chars = 0;
    while (index < due.length && batch.length < MAX_BATCH_ITEMS) {
      const item = due[index];
      const photo = item.photo_file ? await store.readPhoto(item.photo_file).catch(() => null) : null;
      if (batch.length > 0 && chars + (photo?.length ?? 0) > MAX_BATCH_CHARS) break;
      chars += photo?.length ?? 0;
      batch.push(item);
      payload.push({ kind: "MANUAL_REPORT", report: { ...item.report, photo_base64: photo } });
      index += 1;
    }
    let results: SyncItemResult[];
    try {
      results = await sync(payload);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Could not reach the server.";
      const temporary = !(error instanceof ApiError) || isTemporary(error.status);
      items = items.map((i) =>
        batch.some((b) => b.client_report_id === i.client_report_id)
          ? { ...i, state: temporary ? "PENDING" : "FAILED", attempts: i.attempts + 1, last_error: message }
          : i,
      );
      if (temporary) break; // no point trying the next batch now
      failed += batch.length;
      continue;
    }
    for (const result of results) {
      const item = batch[result.index];
      if (!item) continue;
      if (result.ok) {
        synced += 1;
        if (item.photo_file) await store.deletePhoto(item.photo_file).catch(() => undefined);
        items = items.filter((i) => i.client_report_id !== item.client_report_id);
      } else {
        const temporary = isTemporary(result.http_status);
        if (!temporary) failed += 1;
        items = items.map((i) =>
          i.client_report_id === item.client_report_id
            ? {
                ...i,
                state: temporary ? "PENDING" : "FAILED",
                attempts: i.attempts + 1,
                last_error: result.detail ?? `Refused by the server (HTTP ${result.http_status}).`,
              }
            : i,
        );
      }
    }
  }
  return { items, synced, failed };
}
