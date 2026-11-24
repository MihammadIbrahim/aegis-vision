/**
 * Runtime configuration. EXPO_PUBLIC_* variables are inlined by Expo at build time (set them in mobile/.env, which is
 * not committed). The default points an Android emulator at a Core API running on the development machine.
 */
const DEFAULT_API_BASE_URL = "http://10.0.2.2:8000";

function trimSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

export const API_BASE_URL = trimSlash(process.env.EXPO_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL);
export const API_PREFIX = "/api/v1";

/** ws(s)://host/api/v1/realtime/ws, derived from the API address. */
export function websocketUrl(path = `${API_PREFIX}/realtime/ws`): string {
  const base = API_BASE_URL.replace(/^http(s?):\/\//, (_m, s: string) => `ws${s}://`);
  return `${base}${path}`;
}

export const APP_VERSION = "1.0.0";

/** Polling fallback intervals (ms): fast while the WebSocket is down, slow as a safety net while it is up. */
export const POLL_FAST_MS = 5_000;
export const POLL_SLOW_MS = 30_000;
export const REQUEST_TIMEOUT_MS = 20_000;
/** Manual-report photos: the server's default MANUAL_REPORT_PHOTO_MAX_BYTES (5 MB). */
export const MANUAL_REPORT_PHOTO_MAX_BYTES = Number(process.env.EXPO_PUBLIC_MANUAL_REPORT_PHOTO_MAX_BYTES || 5_000_000);
