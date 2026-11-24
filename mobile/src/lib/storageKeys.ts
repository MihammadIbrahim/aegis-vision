/** Every key the Guard app writes on the device (documented in the D-025 SRS section). */
export const SECURE_KEYS = {
  /** The access token (expo-secure-store: Android Keystore / iOS Keychain). Deleted at sign-out. */
  accessToken: "cctv.guard.accessToken",
  /** {operational_mode, dashboard, expires_at} of the current session. Deleted at sign-out. */
  session: "cctv.guard.session",
  /** {guard_id, email, operational_mode} when "Remember login identifier" is on. Never the password. */
  rememberedIdentifier: "cctv.guard.rememberedIdentifier",
  /** The device push token last registered with the server (to unregister it at sign-out). */
  pushToken: "cctv.guard.pushToken",
} as const;

export const STORAGE_KEYS = {
  /** Offline outbox of manual reports (AsyncStorage). Photos are files under documents/outbox/. */
  outbox: "cctv.guard.outbox.v1",
  /** Cached building → floor → area options for the report form (offline use). */
  reportLocations: "cctv.guard.reportLocations.v1",
  /** Time of the last successful sync of the outbox. */
  lastSync: "cctv.guard.lastSync",
} as const;
