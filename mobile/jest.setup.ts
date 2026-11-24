/* Mocks for native modules that do not exist under Jest. Each keeps only what the app uses. */
/* eslint-disable @typescript-eslint/no-require-imports */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"),
);

jest.mock("@react-native-community/netinfo", () => {
  const listeners = new Set<(state: unknown) => void>();
  let state = { isConnected: true, isInternetReachable: true, type: "wifi" };
  return {
    __esModule: true,
    default: {
      addEventListener: (listener: (s: unknown) => void) => {
        listeners.add(listener);
        listener(state);
        return () => listeners.delete(listener);
      },
      fetch: async () => state,
    },
    __setOnline: (online: boolean) => {
      state = { isConnected: online, isInternetReachable: online, type: online ? "wifi" : "none" };
      listeners.forEach((l) => l(state));
    },
  };
});

jest.mock("expo-secure-store", () => {
  const store = new Map<string, string>();
  return {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 1,
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => void store.set(key, value)),
    deleteItemAsync: jest.fn(async (key: string) => void store.delete(key)),
    __store: store,
  };
});

jest.mock("expo-crypto", () => {
  let n = 0;
  return {
    randomUUID: () => {
      n += 1;
      return `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
    },
  };
});

jest.mock("expo-audio", () => ({
  setAudioModeAsync: jest.fn(async () => undefined),
  createAudioPlayer: jest.fn(() => ({ play: jest.fn(), pause: jest.fn(), seekTo: jest.fn(async () => undefined), loop: false, volume: 0 })),
}));

jest.mock("expo-haptics", () => ({
  notificationAsync: jest.fn(async () => undefined),
  NotificationFeedbackType: { Error: "error" },
}));

jest.mock("expo-notifications", () => ({
  setNotificationHandler: jest.fn(),
  setNotificationChannelAsync: jest.fn(async () => null),
  getPermissionsAsync: jest.fn(async () => ({ granted: true })),
  requestPermissionsAsync: jest.fn(async () => ({ granted: true })),
  scheduleNotificationAsync: jest.fn(async () => "local-id"),
  getDevicePushTokenAsync: jest.fn(async () => ({ type: "android", data: "fcm-test-token-0123456789" })),
  AndroidImportance: { MAX: 7 },
  AndroidNotificationVisibility: { PUBLIC: 1 },
  AndroidAudioUsage: { ALARM: 4 },
  AndroidAudioContentType: { SONIFICATION: 4 },
  AndroidNotificationPriority: { MAX: "max" },
}));

jest.mock("expo-device", () => ({ isDevice: true }));

jest.mock("expo-image-picker", () => ({
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  requestMediaLibraryPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchCameraAsync: jest.fn(async () => ({ canceled: true, assets: null })),
  launchImageLibraryAsync: jest.fn(async () => ({ canceled: true, assets: null })),
}));

jest.mock("expo-file-system", () => {
  const files = new Map<string, string>();
  class Directory {
    uri: string;
    constructor(...parts: Array<string | { uri: string }>) {
      this.uri = parts.map((p) => (typeof p === "string" ? p : p.uri)).join("/");
    }
    get exists() {
      return true;
    }
    create() {}
  }
  class File {
    uri: string;
    constructor(...parts: Array<string | { uri: string }>) {
      this.uri = parts.map((p) => (typeof p === "string" ? p : p.uri)).join("/");
    }
    get exists() {
      return files.has(this.uri);
    }
    async write(content: string) {
      files.set(this.uri, content);
    }
    async base64() {
      return files.get(this.uri) ?? "";
    }
    delete() {
      files.delete(this.uri);
    }
  }
  return { Directory, File, Paths: { document: { uri: "file:///documents" } }, __files: files };
});

jest.mock("react-native-safe-area-context", () => require("react-native-safe-area-context/jest/mock").default);

// The alarm vibrates through react-native's Vibration module.
jest.spyOn(require("react-native").Vibration, "vibrate").mockImplementation(() => undefined);
jest.spyOn(require("react-native").Vibration, "cancel").mockImplementation(() => undefined);
