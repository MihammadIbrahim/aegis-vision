import * as Notifications from "expo-notifications";
import { Vibration } from "react-native";

import { ExpoAlarmEngine, VIBRATION_PATTERN } from "../alarm/engine";
import { noticeFor } from "../auth/SessionProvider";
import { createVoiceClient, DEV_BUILD_REQUIRED } from "../calls/voiceClient";
import { websocketUrl } from "../config";
import { alertLocationText, isEscalatedToMe, needsAcknowledgement } from "../lib/format";
import { qk } from "../lib/queryKeys";
import { isGuardId, passwordAcceptable } from "../lib/validation";
import { ALARM_CHANNEL_ID, ensureAlarmChannel } from "../notifications/notifications";
import { invalidationsFor } from "../realtime/RealtimeProvider";
import { alert } from "./utils";

describe("real-time signals → data to read again", () => {
  it("maps each event to exactly what it affects", () => {
    const at = "t";
    expect(invalidationsFor({ type: "event", event: "alert.assigned", alert_id: "a", at })).toEqual([qk.alerts, qk.alert("a"), qk.responders("a")]);
    expect(invalidationsFor({ type: "event", event: "alert.message", alert_id: "a", at })).toEqual([qk.alertMessages("a")]);
    expect(invalidationsFor({ type: "event", event: "incident.message", incident_id: "i", at })).toEqual([qk.incidentMessages("i")]);
    expect(invalidationsFor({ type: "event", event: "incident.invited", incident_id: "i", at })).toEqual([qk.incidents]);
    expect(invalidationsFor({ type: "event", event: "call.invited", call_id: "c", alert_id: "a", at })).toEqual([
      qk.myCalls,
      qk.call("c"),
      qk.activeCall("ALERT", "a"),
    ]);
  });
});

describe("session notices", () => {
  it("explains every forced sign-out", () => {
    expect(noticeFor({ code: "ASSIGNMENT_CHANGED", message: "" }).message).toMatch(/Sign in again and choose the operational mode/);
    expect(noticeFor({ code: "ORGANIZATION_INACTIVE", message: "" }).title).toBe("Organization not active");
    expect(noticeFor({ code: "SESSION_EXPIRED", message: "" }).title).toBe("Session ended");
    expect(noticeFor({ code: "PASSWORD_CHANGED", message: "" }).title).toBe("Password changed");
  });
});

describe("helpers", () => {
  it("accepts both Guard ID formats and the server's password policy", () => {
    expect(isGuardId("abcdef123456789012345678901")).toBe(true);
    expect(isGuardId("GRD-12345678901")).toBe(true);
    expect(isGuardId("SUP-12345678901")).toBe(false);
    expect(passwordAcceptable("Guard-pass-2026", "Guard-pass-2026")).toBe(true);
    expect(passwordAcceptable("onlyletterslong", "onlyletterslong")).toBe(false);
    expect(passwordAcceptable("Guard-pass-2026", "Guard-pass-2027")).toBe(false);
  });

  it("knows when the alarm must ring and when I'm Responding applies", () => {
    expect(needsAcknowledgement(alert())).toBe(true);
    expect(needsAcknowledgement(alert({ myState: "ACKNOWLEDGED" }))).toBe(false);
    expect(needsAcknowledgement(alert({ status: "DISMISSED" }))).toBe(false);
    expect(isEscalatedToMe(alert({ reason: "AUTOMATIC" }))).toBe(true);
    expect(isEscalatedToMe(alert())).toBe(false);
    expect(alertLocationText(alert().location)).toBe("Admin Block → Ground → Lobby");
  });

  it("derives the WebSocket address from the API address", () => {
    expect(websocketUrl()).toBe("ws://10.0.2.2:8000/api/v1/realtime/ws");
  });
});

describe("native adapters", () => {
  it("voice calls fall back to an explanation where native WebRTC is missing (Jest, Expo Go)", async () => {
    const voice = createVoiceClient();
    expect(voice.available).toBe(false);
    expect(voice.unavailableReason).toBe(DEV_BUILD_REQUIRED);
    await expect(voice.connect("wss://x", "t", { onDisconnected: jest.fn(), onParticipantsChanged: jest.fn() })).rejects.toThrow(
      /development build/,
    );
  });

  it("creates a maximum-importance alarm channel with the siren that bypasses Do Not Disturb", async () => {
    const platform = require("react-native").Platform;
    const original = platform.OS;
    platform.OS = "android";
    await ensureAlarmChannel();
    platform.OS = original;
    expect(Notifications.setNotificationChannelAsync).toHaveBeenCalledWith(
      ALARM_CHANNEL_ID,
      expect.objectContaining({ importance: 7, sound: "alarm.wav", bypassDnd: true, lockscreenVisibility: 1 }),
    );
  });

  it("the alarm engine loops the siren at full volume in silent mode and repeats the vibration", async () => {
    const audio = require("expo-audio");
    const engine = new ExpoAlarmEngine();
    await engine.start();
    expect(audio.setAudioModeAsync).toHaveBeenCalledWith({
      playsInSilentMode: true,
      shouldPlayInBackground: true,
      interruptionMode: "doNotMix",
    });
    const player = audio.createAudioPlayer.mock.results[0].value;
    expect(player.loop).toBe(true);
    expect(player.volume).toBe(1);
    expect(player.play).toHaveBeenCalled();
    expect(Vibration.vibrate).toHaveBeenCalledWith(VIBRATION_PATTERN, true);
    await engine.stop();
    expect(player.pause).toHaveBeenCalled();
    expect(Vibration.cancel).toHaveBeenCalled();
    expect(engine.ringing).toBe(false);
  });
});
