import * as Crypto from "expo-crypto";

/** A random UUID for idempotency keys (client_report_id, client_action_id, client_message_id). */
export function newId(): string {
  return Crypto.randomUUID();
}
