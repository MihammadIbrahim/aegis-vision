import type { LoginMethod } from "../api/types";

/** The login method in a platform token's "lm" claim. Display only: the server checks every token
 * itself and allows emergency recovery only in RECOVERY sessions. */
export function loginMethodOf(token: string | null): LoginMethod {
  try {
    const payload = token?.split(".")[1];
    if (!payload) return "NORMAL";
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(payload.length / 4) * 4, "="));
    return JSON.parse(json).lm === "RECOVERY" ? "RECOVERY" : "NORMAL";
  } catch {
    return "NORMAL";
  }
}
