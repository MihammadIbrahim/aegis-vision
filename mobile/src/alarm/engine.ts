/**
 * The alarm sound and vibration (decision D-025). A bundled siren loops at full volume, also in silent mode and in
 * the background, with a repeating vibration, until stop() is called. The engine is an interface so tests (and
 * environments without native audio) can use a recording fake.
 */
import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from "expo-audio";
import * as Haptics from "expo-haptics";
import { Vibration } from "react-native";

export interface AlarmEngine {
  readonly ringing: boolean;
  start(): Promise<void>;
  stop(): Promise<void>;
}

export const VIBRATION_PATTERN = [0, 900, 400, 900, 400, 900, 1200];

 
const SIREN = require("../../assets/sounds/alarm.wav");

export class ExpoAlarmEngine implements AlarmEngine {
  private player: AudioPlayer | null = null;
  private active = false;

  get ringing(): boolean {
    return this.active;
  }

  async start(): Promise<void> {
    if (this.active) return;
    this.active = true;
    try {
      await setAudioModeAsync({ playsInSilentMode: true, shouldPlayInBackground: true, interruptionMode: "doNotMix" });
      if (!this.player) this.player = createAudioPlayer(SIREN);
      this.player.loop = true;
      this.player.volume = 1;
      await this.player.seekTo(0);
      this.player.play();
    } catch {
      // Audio unavailable (e.g. a simulator without output): vibration still runs.
    }
    Vibration.vibrate(VIBRATION_PATTERN, true);
    void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => undefined);
  }

  async stop(): Promise<void> {
    if (!this.active) return;
    this.active = false;
    Vibration.cancel();
    try {
      this.player?.pause();
    } catch {
      // nothing to stop
    }
  }
}
