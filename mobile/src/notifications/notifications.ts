/**
 * Local and push notifications (decision D-025).
 *
 * * Android channel ``security-alarm``: maximum importance, the bundled siren, a long vibration, Do Not Disturb bypass
 *   where the system allows it, visible on the lock screen, alarm audio usage. The server's FCM pushes name this
 *   channel. A channel's sound cannot change after creation, so the ID is versioned in the code if it ever must.
 * * The device push token (FCM on Android, APNs on iOS) is registered with POST /guard/device-tokens and removed at
 *   sign-out. Remote push needs a development build with Firebase configured; Expo Go cannot receive it.
 */
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import {
  AndroidAudioContentType,
  AndroidAudioUsage,
  AndroidImportance,
  AndroidNotificationPriority,
  AndroidNotificationVisibility,
} from "expo-notifications";
import { Platform } from "react-native";

import { guardApi } from "../api/endpoints";
import { APP_VERSION } from "../config";
import { secureDelete, secureGet, secureSet } from "../lib/secure";
import { SECURE_KEYS } from "../lib/storageKeys";

export const ALARM_CHANNEL_ID = "security-alarm";
export const ALARM_SOUND = "alarm.wav";

let handlerInstalled = false;

/** Notifications shown while the app is open (the in-app alarm takes over for alerts). */
export function installNotificationHandler(): void {
  if (handlerInstalled) return;
  handlerInstalled = true;
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

export async function ensureAlarmChannel(): Promise<void> {
  if (Platform.OS !== "android") return;
  await Notifications.setNotificationChannelAsync(ALARM_CHANNEL_ID, {
    name: "Security alarms",
    description: "Unacknowledged security alerts. Rings until you acknowledge in the Guard app.",
    importance: AndroidImportance.MAX,
    sound: ALARM_SOUND,
    vibrationPattern: [0, 900, 400, 900, 400, 900],
    enableVibrate: true,
    bypassDnd: true,
    lockscreenVisibility: AndroidNotificationVisibility.PUBLIC,
    audioAttributes: {
      usage: AndroidAudioUsage.ALARM,
      contentType: AndroidAudioContentType.SONIFICATION,
    },
  });
}

export async function requestNotificationPermission(): Promise<boolean> {
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  const asked = await Notifications.requestPermissionsAsync({
    ios: { allowAlert: true, allowSound: true, allowBadge: false, allowCriticalAlerts: true },
  });
  return asked.granted;
}

/** A loud local notification for an alert that arrived while the app was in the background. */
export async function notifyAlarmInBackground(alertId: string, title: string, body: string): Promise<void> {
  await Notifications.scheduleNotificationAsync({
    content: {
      title,
      body,
      sound: ALARM_SOUND,
      priority: AndroidNotificationPriority.MAX,
      data: { kind: "ALERT_ALARM", alert_id: alertId },
    },
    trigger: Platform.OS === "android" ? { channelId: ALARM_CHANNEL_ID } : null,
  });
}

export type PushRegistration =
  | { status: "registered"; provider: "FCM" | "APNS"; pushDeliveryMode: "none" | "fcm" }
  | { status: "unavailable"; reason: string };

/** Registers this phone's push token with the server (best effort; never blocks sign-in). */
export async function registerPushToken(): Promise<PushRegistration> {
  if (!Device.isDevice) return { status: "unavailable", reason: "Push notifications need a physical phone." };
  if (Constants.executionEnvironment === "storeClient") {
    return { status: "unavailable", reason: "Expo Go cannot receive remote push; use the development build." };
  }
  if (!(await requestNotificationPermission())) {
    return { status: "unavailable", reason: "Notifications are not allowed in the phone's settings." };
  }
  try {
    const token = await Notifications.getDevicePushTokenAsync();
    const platform = Platform.OS === "ios" ? "IOS" : "ANDROID";
    const provider = platform === "IOS" ? "APNS" : "FCM";
    const data = typeof token.data === "string" ? token.data : JSON.stringify(token.data);
    const saved = await guardApi.registerDevice({ token: data, platform, provider, app_version: APP_VERSION });
    await secureSet(SECURE_KEYS.pushToken, data);
    return { status: "registered", provider, pushDeliveryMode: saved.push_delivery_mode };
  } catch {
    return { status: "unavailable", reason: "This build has no push configuration (Firebase / APNs) yet." };
  }
}

/** Removes the token from the server and the device (sign-out). */
export async function forgetPushToken(): Promise<void> {
  const token = await secureGet<string>(SECURE_KEYS.pushToken);
  if (!token) return;
  try {
    await guardApi.forgetDevice(token);
  } finally {
    await secureDelete(SECURE_KEYS.pushToken);
  }
}
