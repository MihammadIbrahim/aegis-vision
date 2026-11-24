// Live monitoring, recordings, retention and the evidence vault (decision D-023). The mocked URLs,
// methods and bodies are the Core API's (recording_router.py, storage_settings_router.py,
// evidence_router.py), so these tests also pin the contract.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../app/App";
import { StaticAuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";
import type { Camera } from "../features/cameras";
import { visibleRange } from "../features/video/components/TimelineBar";
import { formatBytes, formatCountdown, formatDuration, formatRetention, formatSeconds } from "../features/video/labels";
import { EvidenceDetailPage } from "../features/video/pages/EvidenceDetailPage";
import { EvidencePage } from "../features/video/pages/EvidencePage";
import { LivePage } from "../features/video/pages/LivePage";
import { RecordingsPage } from "../features/video/pages/RecordingsPage";
import type { EvidenceItem, MonitoredCamera, StorageOverview, Timeline } from "../features/video/types";
import { mockApi, renderAs, sent, sessionFor } from "./helpers";

const camera = (id: string, name: string): Camera => ({
  id, campus_id: "k1", display_name: name, connection_type: "RTSP", stream_url: `rtsp://10.0.0.${id.length}/s`,
  environment: null, location: null, created_at: null, updated_at: null,
});
const status = (state: "ONLINE" | "OFFLINE" | "UNKNOWN", last_error: string | null = null) => ({
  state, last_error, last_seen_at: null, source: "INGESTION" as const, state_changed_at: null, reported_at: null,
});
const LIVE: MonitoredCamera[] = [
  { camera: camera("c1", "Gate"), status: status("ONLINE") },
  { camera: camera("c2", "Lobby"), status: status("OFFLINE", "The connection was refused.") },
  { camera: camera("c3", "Roof"), status: status("UNKNOWN", "No stream URL is configured.") },
  { camera: camera("c4", "Yard"), status: status("ONLINE") },
  { camera: camera("c5", "Dock"), status: status("ONLINE") },
];
const treeRoute = { method: "GET", url: /\/locations$/, body: [] };

/** jsdom has no object URLs; the pages only need distinct strings. */
beforeEach(() => {
  let n = 0;
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => `blob:test-${++n}`), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

/** A JPEG-looking response for the live frame endpoint. */
function frameRoute(id: string, ok = true) {
  return ok
    ? { method: "GET", url: new RegExp(`/live/cameras/${id}/frame$`), body: "jpeg" }
    : { method: "GET", url: new RegExp(`/live/cameras/${id}/frame$`), status: 404, body: { detail: "The live frame is stale; the camera may be offline.", code: "LIVE_FRAME_STALE" } };
}

describe("Live Cameras", () => {
  it("audits the view, shows a 2×2 grid by default with statuses, pages and switches to 3×3", async () => {
    const calls = mockApi([
      treeRoute,
      { method: "POST", url: /\/live\/sessions$/, body: LIVE },
      { method: "GET", url: /\/live\/cameras$/, body: LIVE },
      frameRoute("c1"), frameRoute("c2", false), frameRoute("c3", false), frameRoute("c4"), frameRoute("c5"),
    ]);
    renderAs("SUPERVISOR", <LivePage />);
    expect(await screen.findByText("3 of 5 cameras online · 1 offline · frames refresh every second")).toBeTruthy();
    expect(sent(calls, "POST", /\/live\/sessions$/)[0].body).toEqual({ camera_ids: [] });
    const grid = screen.getByRole("list", { name: "Live cameras" });
    expect(within(grid).getAllByRole("listitem")).toHaveLength(4);
    expect(await within(grid).findByAltText("Live view of Gate")).toBeTruthy();
    // A camera without frames shows an explicit connection error, not a frozen picture.
    expect(await within(grid).findAllByText("No recent frames; the camera may be offline.")).toHaveLength(2);
    expect(within(grid).getByText("The connection was refused.")).toBeTruthy();
    expect(screen.getByText("Page 1 of 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "3×3" }));
    expect(within(screen.getByRole("list", { name: "Live cameras" })).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.queryByText(/Page \d of/)).toBeNull();
  });

  it("filters by status and opens one camera fullscreen (audited for that camera)", async () => {
    const calls = mockApi([
      treeRoute,
      { method: "POST", url: /\/live\/sessions$/, body: LIVE },
      { method: "GET", url: /\/live\/cameras$/, body: LIVE },
      frameRoute("c1"), frameRoute("c2", false), frameRoute("c3", false), frameRoute("c4"), frameRoute("c5"),
    ]);
    renderAs("ADMIN", <LivePage />);
    await screen.findByText(/of 5 cameras online/);
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "OFFLINE" } });
    const grid = screen.getByRole("list", { name: "Live cameras" });
    expect(within(grid).getAllByRole("listitem")).toHaveLength(1);
    fireEvent.click(within(grid).getByRole("button", { name: "Fullscreen Lobby" }));
    expect(screen.getByRole("dialog", { name: "Lobby fullscreen" })).toBeTruthy();
    await waitFor(() => expect(sent(calls, "POST", /\/live\/sessions$/).map((c) => c.body)).toEqual([{ camera_ids: [] }, { camera_ids: ["c2"] }]));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "Lobby fullscreen" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "UNKNOWN" } });
    expect(within(screen.getByRole("list", { name: "Live cameras" })).getByText("No stream URL is configured.")).toBeTruthy();
  });

  it("marks cameras with open alerts (never a claim that AI is running) and searches by name or ID", async () => {
    const openAlert = (id: string, camera: string, alert_type: string, status: string, priority = "HIGH") => ({
      id, camera_id: camera, alert_type, status, priority, camera_name: "x", location: {}, confidence: null,
      detected_at: T0, last_detected_at: T0, detection_count: 1, close_note: null, closed_at: null, created_at: T0, updated_at: T0, recipients: [],
    });
    const calls = mockApi([
      treeRoute,
      { method: "POST", url: /\/live\/sessions$/, body: LIVE },
      { method: "GET", url: /\/live\/cameras(\?.*)?$/, body: LIVE },
      { method: "GET", url: /\/alerts\?/, body: [openAlert("al1", "c1", "WEAPON", "NEW"), openAlert("al2", "c4", "FIGHT_VIOLENCE", "RESOLVED")] },
      frameRoute("c1"), frameRoute("c2", false), frameRoute("c3", false), frameRoute("c4"), frameRoute("c5"),
    ]);
    renderAs("ADMIN", <LivePage />);
    const grid = await screen.findByRole("list", { name: "Live cameras" });
    const gate = await within(grid).findByRole("figure", { name: /^Gate: .*1 open alert$/ });
    expect(within(gate).getByText("Weapon detected")).toBeTruthy();
    expect(gate.className).toContain("has-alert");
    expect(within(gate).getByText("c1")).toBeTruthy(); // the Camera ID under the name
    const yard = within(grid).getByRole("figure", { name: /^Yard: / });
    expect(within(yard).queryByText("Fight / violence")).toBeNull(); // a resolved alert is not shown
    expect(screen.queryByText(/AI monitoring/)).toBeNull();
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "lobby" } });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/live/cameras?q=lobby"))).toBe(true));
  });

  it("shows an empty state and the server's error", async () => {
    mockApi([treeRoute, { method: "POST", url: /\/live\/sessions$/, body: [] }, { method: "GET", url: /\/live\/cameras$/, body: [] }]);
    const { unmount } = renderAs("ADMIN", <LivePage />);
    expect(await screen.findByText("No cameras to show")).toBeTruthy();
    unmount();
    mockApi([treeRoute, { method: "POST", url: /\/live\/sessions$/, body: [] }, { method: "GET", url: /\/live\/cameras$/, status: 403, body: { detail: "Only Admins and Supervisors can use this." } }]);
    renderAs("ADMIN", <LivePage />);
    expect(await screen.findByText("Only Admins and Supervisors can use this.")).toBeTruthy();
  });
});

const T0 = "2026-11-16T10:00:00Z";
const TIMELINE: Timeline = {
  since: "2026-11-16T09:00:00Z",
  until: "2026-11-16T11:00:00Z",
  retention_seconds: 7 * 86400,
  cameras: [LIVE[0], LIVE[1]],
  segments: [
    { id: "s2", camera_id: "c1", camera_name: "Gate", started_at: "2026-11-16T10:01:00Z", ended_at: "2026-11-16T10:02:00Z", duration_seconds: 60, size_bytes: 2_500_000, expires_at: "2026-11-23T10:02:00Z" },
    { id: "s1", camera_id: "c1", camera_name: "Gate", started_at: T0, ended_at: "2026-11-16T10:01:00Z", duration_seconds: 60, size_bytes: 2_400_000, expires_at: "2026-11-23T10:01:00Z" },
  ],
  markers: [
    { alert_id: "al1", camera_id: "c1", alert_type: "WEAPON", priority: "HIGH", status: "NEW", detected_at: "2026-11-16T10:00:30Z", last_detected_at: "2026-11-16T10:00:35Z" },
  ],
};
const retention = (value: number, unit: "MINUTES" | "HOURS" | "DAYS", is_default = false) => ({
  value, unit, seconds: value * { MINUTES: 60, HOURS: 3600, DAYS: 86400 }[unit], is_default,
  updated_at: is_default ? null : "2026-11-16T08:00:00Z", updated_by: is_default ? null : "u1", updated_by_name: is_default ? null : "Ayesha Khan",
});
const OVERVIEW: StorageOverview = {
  retention: retention(7, "DAYS", true),
  default_retention: retention(7, "DAYS", true),
  storage: { node: "primary", total_bytes: 1e12, used_bytes: 8.5e11, free_bytes: 1.5e11, percent_used: 85, level: "WARNING", reported_at: T0 },
  usage: { recording_segments: 2, recording_bytes: 4_900_000, oldest_recording_at: T0, evidence_items: 1, evidence_bytes: 1_200_000 },
  provider: "LOCAL_DISK",
  warning_percent: 80,
  critical_percent: 90,
};

describe("Recordings", () => {
  const routes = () => [
    treeRoute,
    { method: "GET", url: /\/recordings\/retention$/, body: OVERVIEW },
    { method: "GET", url: /\/recordings\?/, body: TIMELINE },
    { method: "GET", url: /\/recordings\/s\d\/media$/, body: "mp4" },
  ];

  it("lists segments with AI markers, deletion countdown and storage, plays one and jumps to its event", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-11-16T12:00:00Z").getTime());
    const calls = mockApi(routes());
    renderAs("SUPERVISOR", <RecordingsPage />);
    const table = await screen.findByRole("table", { name: "Recording segments" });
    expect(within(table).getAllByText("Gate")).toHaveLength(2);
    expect(within(table).getAllByText("1:00")).toHaveLength(2);
    expect(screen.getByText("2 segments · 1 AI event", { exact: false })).toBeTruthy();
    // No period chosen: the server's "last 24 hours up to now" applies on every load (Refresh shows new footage).
    const query = calls.find((c) => /\/recordings\?/.test(c.url))!.url;
    expect(query).toMatch(/limit=500/);
    expect(query).not.toMatch(/since=|until=/);
    expect(screen.getByText("Showing the last 24 hours")).toBeTruthy();
    // The deletion countdown of each segment (Supervisor spec page 8) and the retention in force.
    expect(within(table).getAllByText("in 6 d 22 h")).toHaveLength(2);
    expect(screen.getByText(/each kept for 7 days after recording/)).toBeTruthy();
    // Storage: retention (default), usage by tier, provider, low-storage warning. Supervisors may change the
    // retention too (requirements Section 17).
    expect(await screen.findByText("7 days")).toBeTruthy();
    expect(screen.getByText("Low storage")).toBeTruthy();
    expect(screen.getByText(/2 files · 4\.7 MB/)).toBeTruthy();
    expect(screen.getByText(/1 item · 1\.1 MB/)).toBeTruthy();
    expect(screen.getByText("Local disk on the storage server")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Change retention" })).toBeTruthy();
    // The AI marker opens the segment that covers it.
    fireEvent.click(screen.getByRole("button", { name: /Weapon detected at/ }));
    expect(await screen.findByLabelText("Recording of Gate")).toBeTruthy();
    expect(sent(calls, "GET", /\/recordings\/s1\/media$/)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "weapon at 0:30" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save clip" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("saves a snapshot from playback into the evidence vault", async () => {
    const saved = { id: "e1", title: "Gate snapshot" };
    const calls = mockApi([...routes(), { method: "POST", url: /\/evidence\/from-playback$/, status: 201, body: saved }]);
    renderAs("ADMIN", <RecordingsPage />);
    fireEvent.click(await screen.findAllByRole("button", { name: "Play" }).then((b) => b[1]));
    await screen.findByLabelText("Recording of Gate");
    fireEvent.click(screen.getByRole("button", { name: "Save snapshot" }));
    const dialog = screen.getByRole("dialog", { name: "Save to evidence vault" });
    expect(within(dialog).queryByRole("option", { name: "Video clip" })).toBeNull(); // no in/out marked
    fireEvent.change(within(dialog).getByLabelText(/Title/), { target: { value: "Gate snapshot" } });
    fireEvent.change(within(dialog).getByLabelText(/Investigation reference/), { target: { value: "INV-7" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save evidence" }));
    await waitFor(() => expect(sent(calls, "POST", /from-playback$/)).toHaveLength(1));
    expect(sent(calls, "POST", /from-playback$/)[0].body).toEqual({
      segment_id: "s1", kind: "SNAPSHOT", start_offset_seconds: 0, end_offset_seconds: null,
      title: "Gate snapshot", notes: null, investigation_ref: "INV-7", tags: [], incident_id: null,
    });
    expect(await screen.findByText('"Gate snapshot" saved to the evidence vault.')).toBeTruthy();
  });

  it("lets a Supervisor change retention in minutes with their password (PUT /recordings/retention)", async () => {
    const calls = mockApi([
      ...routes(),
      { method: "PUT", url: /\/recordings\/retention$/, body: { ...OVERVIEW, retention: retention(30, "MINUTES") } },
    ]);
    renderAs("SUPERVISOR", <RecordingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Change retention" }));
    const dialog = screen.getByRole("dialog", { name: "Recording retention" });
    expect(within(dialog).getByText(/Evidence in the vault is never deleted by retention/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/Keep recordings for/), { target: { value: "30" } });
    fireEvent.change(within(dialog).getByLabelText(/Unit/), { target: { value: "MINUTES" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save retention" }));
    expect(await within(dialog).findByText("Your password is required to change the retention.")).toBeTruthy();
    expect(sent(calls, "PUT", /retention$/)).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText(/Your password/), { target: { value: "Secret-pass-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save retention" }));
    await waitFor(() => expect(sent(calls, "PUT", /retention$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /retention$/)[0].body).toEqual({ value: 30, unit: "MINUTES", current_password: "Secret-pass-1" });
    expect(await screen.findByText("Recordings are now kept for 30 minutes.")).toBeTruthy();
  });

  it("shows the server's refusal of a wrong password inside the dialog", async () => {
    mockApi([
      ...routes(),
      { method: "PUT", url: /\/recordings\/retention$/, status: 403, body: { detail: "The current password is incorrect.", code: "CURRENT_PASSWORD_INCORRECT" } },
    ]);
    renderAs("ADMIN", <RecordingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Change retention" }));
    const dialog = screen.getByRole("dialog", { name: "Recording retention" });
    fireEvent.change(within(dialog).getByLabelText(/Your password/), { target: { value: "wrong" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save retention" }));
    expect(await within(dialog).findByText("The current password is incorrect.")).toBeTruthy();
  });
});

const ITEM: EvidenceItem = {
  id: "e1", kind: "CLIP", title: "Fight at the gate", notes: "Two persons", investigation_ref: "INV-7", source: "ALERT",
  alert_id: "al1", camera_id: "c1", camera_name: "Gate", covers_from: T0, covers_to: "2026-11-16T10:00:40Z",
  media_type: "video/mp4", size_bytes: 1_200_000, sha256: "a".repeat(64), saved_by: "u2", saved_by_name: "Sana Iqbal",
  saved_at: T0, updated_at: T0,
};

describe("Evidence Vault", () => {
  it("lists and searches evidence by text, type, camera and date (GET /evidence?q=&kind=&camera_id=&since=&until=)", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/evidence(\?.*)?$/, body: [ITEM] },
      { method: "GET", url: /\/v1\/cameras$/, body: [camera("c1", "Gate")] },
    ]);
    renderAs("SUPERVISOR", <EvidencePage />);
    const table = await screen.findByRole("table", { name: "Evidence" });
    expect(within(table).getByRole("link", { name: "Fight at the gate" }).getAttribute("href")).toBe("/evidence/e1");
    expect(within(table).getByText("INV-7")).toBeTruthy();
    expect(within(table).getByRole("link", { name: "Alert" }).getAttribute("href")).toBe("/alerts/al1");
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "INV" } });
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "CLIP" } });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/evidence?q=INV&kind=CLIP&limit=200"))).toBe(true));
    await waitFor(() => expect((screen.getByLabelText("Camera") as HTMLSelectElement).options.length).toBe(2));
    fireEvent.change(screen.getByLabelText("Camera"), { target: { value: "c1" } });
    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-11-16" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-11-16" } });
    await waitFor(() => {
      const url = calls[calls.length - 1].url;
      expect(url).toMatch(/camera_id=c1/);
      expect(url).toMatch(/since=2026-11-1[56]T\d\d%3A00%3A00\.000Z/);
      expect(url).toMatch(/until=2026-11-1[67]T\d\d%3A59%3A59\.000Z/);
    });
  });

  it("shows the clip, saves edited details (PATCH) and hides deletion from Supervisors", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/evidence\/e1$/, body: ITEM },
      { method: "GET", url: /\/evidence\/e1\/media$/, body: "mp4" },
      { method: "PATCH", url: /\/evidence\/e1$/, body: { ...ITEM, notes: "Two persons; knife" } },
    ]);
    renderAs("SUPERVISOR", <EvidenceDetailPage />, "/evidence/e1", "/evidence/:id");
    expect(await screen.findByLabelText("Evidence clip: Fight at the gate")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete evidence" })).toBeNull();
    const save = screen.getByRole("button", { name: "Save details" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Notes/), { target: { value: "Two persons; knife" } });
    fireEvent.click(save);
    await waitFor(() => expect(sent(calls, "PATCH", /\/evidence\/e1$/)).toHaveLength(1));
    expect(sent(calls, "PATCH", /\/evidence\/e1$/)[0].body).toEqual({ title: "Fight at the gate", notes: "Two persons; knife", investigation_ref: "INV-7", tags: [] });
    expect(await screen.findByText("Evidence details saved.")).toBeTruthy();
  });

  it("lets an Admin delete with DELETE typed and their password (POST /evidence/{id}/delete)", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/evidence\/e1$/, body: ITEM },
      { method: "GET", url: /\/evidence\/e1\/media$/, body: "mp4" },
      { method: "POST", url: /\/evidence\/e1\/delete$/, status: 204, body: null },
      { method: "GET", url: /\/evidence(\?.*)?$/, body: [] },
    ]);
    render(
      <MemoryRouter initialEntries={["/evidence/e1"]}>
        <StaticAuthProvider session={sessionFor("ADMIN")}>
          <ToastProvider>
            <App />
          </ToastProvider>
        </StaticAuthProvider>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Delete evidence" }));
    const dialog = screen.getByRole("dialog", { name: 'Delete evidence "Fight at the gate"?' });
    fireEvent.change(within(dialog).getByLabelText(/Your password/), { target: { value: "Secret-pass-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete permanently" }));
    expect(await within(dialog).findByText("Type DELETE to confirm.")).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/Type DELETE to confirm/), { target: { value: "DELETE" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete permanently" }));
    await waitFor(() => expect(sent(calls, "POST", /\/delete$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/delete$/)[0].body).toEqual({ current_password: "Secret-pass-1" });
    expect(await screen.findByRole("heading", { name: "Evidence Vault", level: 1 }, { timeout: 5000 })).toBeTruthy();
  });
});

describe("timeline range", () => {
  it("zooms to the recorded part of a long period, with padding, and keeps short periods whole", () => {
    const zoom = visibleRange(TIMELINE.since, TIMELINE.until, TIMELINE.segments, TIMELINE.markers);
    expect(zoom.zoomed).toBe(true);
    expect(new Date(zoom.start).toISOString()).toBe("2026-11-16T09:59:30.000Z"); // 10:00 minus 30 s padding
    expect(new Date(zoom.end).toISOString()).toBe("2026-11-16T10:02:30.000Z");
    const whole = visibleRange("2026-11-16T10:00:00Z", "2026-11-16T10:02:00Z", TIMELINE.segments, []);
    expect(whole).toEqual({ start: Date.parse("2026-11-16T10:00:00Z"), end: Date.parse("2026-11-16T10:02:00Z"), zoomed: false });
    expect(visibleRange(TIMELINE.since, TIMELINE.until, [], []).zoomed).toBe(false);
  });
});

describe("video labels", () => {
  it("formats retention, sizes and durations", () => {
    expect(formatRetention(retention(1, "HOURS"))).toBe("1 hour");
    expect(formatRetention(retention(45, "MINUTES"))).toBe("45 minutes");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2_400_000)).toBe("2.3 MB");
    expect(formatDuration(59.6)).toBe("1:00");
    expect(formatDuration(3725)).toBe("1:02:05");
    expect(formatSeconds(300)).toBe("5 minutes");
    expect(formatSeconds(7200)).toBe("2 hours");
    expect(formatSeconds(86400)).toBe("1 day");
    const now = new Date("2026-11-16T12:00:00Z").getTime();
    expect(formatCountdown("2026-11-16T12:04:30Z", now)).toBe("in 4 min");
    expect(formatCountdown("2026-11-16T15:30:00Z", now)).toBe("in 3 h 30 min");
    expect(formatCountdown("2026-11-16T11:00:00Z", now)).toBe("due now");
    expect(formatCountdown(null, now)).toBe("—");
  });
});
