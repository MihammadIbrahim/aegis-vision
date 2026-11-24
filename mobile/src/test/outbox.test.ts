import { ApiError } from "../api/client";
import type { SyncItem, SyncItemResult } from "../api/types";
import { flushOutbox, type OutboxItem } from "../offline/outbox";
import { memoryOutboxStore } from "./utils";

function item(id: string, owner = "GUARD-1", state: OutboxItem["state"] = "PENDING", photo: string | null = null): OutboxItem {
  return {
    client_report_id: id,
    owner_guard_id: owner,
    report: { client_report_id: id, report_type: "Trespassing", description: "Fence cut", building_id: "b1" },
    photo_file: photo,
    state,
    attempts: 0,
    last_error: null,
    queued_at: "2026-11-19T10:00:00Z",
    display: { building_name: "North Fence", floor_name: null, area_name: null },
  };
}

const result = (index: number, okay: boolean, status: number, detail: string | null = null): SyncItemResult => ({
  index,
  kind: "MANUAL_REPORT",
  ok: okay,
  http_status: status,
  code: null,
  detail,
  outcome: okay ? "CREATED" : null,
  target_id: null,
});

describe("Offline outbox", () => {
  it("sends the owner's pending reports through /guard/sync with their client_report_id and photo", async () => {
    const store = memoryOutboxStore();
    store.photos.set("mem://r1", "iVBORw0KGgo=");
    const sync = jest.fn(async (items: SyncItem[]) => items.map((_, i) => result(i, true, 201)));
    const out = await flushOutbox([item("r1", "GUARD-1", "PENDING", "mem://r1"), item("r2", "OTHER")], "GUARD-1", store, sync);
    expect(sync).toHaveBeenCalledWith([
      {
        kind: "MANUAL_REPORT",
        report: expect.objectContaining({ client_report_id: "r1", photo_base64: "iVBORw0KGgo=" }),
      },
    ]);
    // Another Guard's queued report is never sent with this Guard's session.
    expect(out.items.map((i) => i.client_report_id)).toEqual(["r2"]);
    expect(out.synced).toBe(1);
    expect(store.photos.has("mem://r1")).toBe(false);
  });

  it("marks refused items FAILED with the server's reason and keeps temporary failures PENDING", async () => {
    const sync = jest.fn(async () => [result(0, false, 422, "Unknown building."), result(1, false, 503)]);
    const out = await flushOutbox([item("a"), item("b")], "GUARD-1", memoryOutboxStore(), sync);
    expect(out.items.find((i) => i.client_report_id === "a")).toMatchObject({ state: "FAILED", last_error: "Unknown building." });
    expect(out.items.find((i) => i.client_report_id === "b")).toMatchObject({ state: "PENDING", attempts: 1 });
  });

  it("keeps everything PENDING when the network is down, and retries FAILED items only on request", async () => {
    const down = jest.fn(async (_items: SyncItem[]): Promise<SyncItemResult[]> => {
      throw new ApiError(0, "offline", "NETWORK_ERROR");
    });
    const out = await flushOutbox([item("a"), item("f", "GUARD-1", "FAILED")], "GUARD-1", memoryOutboxStore(), down);
    expect(out.items.map((i) => i.state)).toEqual(["PENDING", "FAILED"]);
    expect(down.mock.calls[0][0]).toHaveLength(1);
    const again = jest.fn(async (items: SyncItem[]) => items.map((_, i) => result(i, true, 200)));
    const retried = await flushOutbox(out.items, "GUARD-1", memoryOutboxStore(), again, true);
    expect(again.mock.calls[0][0]).toHaveLength(2);
    expect(retried.items).toHaveLength(0);
  });

  it("is idempotent: the same client_report_id is sent on every retry", async () => {
    const seen: string[] = [];
    const flaky = jest.fn(async (items: SyncItem[]) => {
      items.forEach((i) => i.kind === "MANUAL_REPORT" && seen.push(i.report.client_report_id));
      if (seen.length === 1) throw new ApiError(0, "offline", "NETWORK_ERROR");
      return items.map((_, i) => ({ ...result(i, true, 200), outcome: "REPLAYED" }));
    });
    const first = await flushOutbox([item("same")], "GUARD-1", memoryOutboxStore(), flaky);
    await flushOutbox(first.items, "GUARD-1", memoryOutboxStore(), flaky);
    expect(seen).toEqual(["same", "same"]);
  });
});
