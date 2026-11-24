import type { CameraStatus, Retention, RetentionUnit } from "./types";

export const UNIT_LABELS: Record<RetentionUnit, string> = { MINUTES: "Minutes", HOURS: "Hours", DAYS: "Days" };

export function formatRetention(r: Retention): string {
  const unit = UNIT_LABELS[r.unit].toLowerCase();
  return `${r.value} ${r.value === 1 ? unit.slice(0, -1) : unit}`;
}

/** A retention in seconds in the largest whole unit: "5 minutes", "2 hours", "7 days". */
export function formatSeconds(seconds: number): string {
  const [value, unit] = seconds % 86400 === 0 ? [seconds / 86400, "day"] : seconds % 3600 === 0 ? [seconds / 3600, "hour"] : [Math.round(seconds / 60), "minute"];
  return `${value} ${unit}${value === 1 ? "" : "s"}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}

export function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

/** "in 6 d 23 h", "in 4 min" or "due now": the deletion countdown of a routine recording. */
export function formatCountdown(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  if (seconds <= 0) return "due now";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `in ${d} d ${h} h`;
  if (h > 0) return `in ${h} h ${m} min`;
  return `in ${Math.max(1, m)} min`;
}

/** Why a camera is not online, in the operator's words (never an address). */
export function statusHint(status: CameraStatus): string {
  if (status.state === "ONLINE") return "Receiving frames";
  if (status.state === "OFFLINE") return status.last_error ?? "Not receiving frames";
  return status.last_error ?? "No status reported yet";
}

/** Grid sizes offered on the live page: 1×1, 2×2, 3×3, 4×4 (requirements 13.3). */
export const GRID_SIZES = [1, 2, 3, 4] as const;
export type GridSize = (typeof GRID_SIZES)[number];
