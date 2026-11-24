/**
 * Audio transport for in-app calls (decision D-025), wrapped so the app works where native WebRTC is missing.
 *
 * LiveKit's React Native SDK needs native code, so live audio works only in a development build of the Guard app
 * (`npx expo run:android` / `eas build --profile development`), not in Expo Go and not in Jest. There the adapter
 * reports `available: false` with the reason, and the call buttons explain it instead of crashing.
 */
import { Platform } from "react-native";

export interface VoiceEvents {
  /** Identities (account IDs) of the other people currently connected to the room. */
  onParticipantsChanged: (identities: string[]) => void;
  onDisconnected: () => void;
  onReconnecting?: () => void;
  onReconnected?: () => void;
}

export interface VoiceClient {
  readonly available: boolean;
  readonly unavailableReason: string | null;
  connect(url: string, token: string, events: VoiceEvents): Promise<void>;
  disconnect(): Promise<void>;
  setMicrophoneEnabled(enabled: boolean): Promise<void>;
  setSpeakerphone(on: boolean): Promise<void>;
}

export const DEV_BUILD_REQUIRED =
  "Live call audio needs the development build of the Guard app (LiveKit uses native WebRTC, which Expo Go does not include).";

export class UnavailableVoiceClient implements VoiceClient {
  readonly available = false;
  constructor(readonly unavailableReason: string = DEV_BUILD_REQUIRED) {}
  async connect(): Promise<void> {
    throw new Error(this.unavailableReason);
  }
  async disconnect(): Promise<void> {}
  async setMicrophoneEnabled(): Promise<void> {}
  async setSpeakerphone(): Promise<void> {}
}

/* eslint-disable @typescript-eslint/no-require-imports */
type LiveKitNative = typeof import("@livekit/react-native");
type LiveKitClient = typeof import("livekit-client");

let globalsRegistered = false;

class LiveKitVoiceClient implements VoiceClient {
  readonly available = true;
  readonly unavailableReason = null;
  private room: InstanceType<LiveKitClient["Room"]> | null = null;

  constructor(
    private readonly native: LiveKitNative,
    private readonly client: LiveKitClient,
  ) {}

  async connect(url: string, token: string, events: VoiceEvents): Promise<void> {
    const { AudioSession } = this.native;
    const { Room, RoomEvent } = this.client;
    await AudioSession.configureAudio({
      android: { preferredOutputList: ["earpiece", "headset", "bluetooth", "speaker"], audioTypeOptions: {} },
      ios: { defaultOutput: "earpiece" },
    });
    await AudioSession.startAudioSession();
    const room = new Room({ adaptiveStream: false, dynacast: false });
    this.room = room;
    const report = () => events.onParticipantsChanged(Array.from(room.remoteParticipants.values()).map((p) => p.identity));
    room.on(RoomEvent.ParticipantConnected, report);
    room.on(RoomEvent.ParticipantDisconnected, report);
    room.on(RoomEvent.Reconnecting, () => events.onReconnecting?.());
    room.on(RoomEvent.Reconnected, () => events.onReconnected?.());
    room.on(RoomEvent.Disconnected, () => events.onDisconnected());
    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);
    report();
  }

  async disconnect(): Promise<void> {
    const room = this.room;
    this.room = null;
    try {
      await room?.disconnect();
    } finally {
      await this.native.AudioSession.stopAudioSession().catch(() => undefined);
    }
  }

  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    await this.room?.localParticipant.setMicrophoneEnabled(enabled);
  }

  async setSpeakerphone(on: boolean): Promise<void> {
    const output = Platform.OS === "ios" ? (on ? "force_speaker" : "default") : on ? "speaker" : "earpiece";
    await this.native.AudioSession.selectAudioOutput(output);
  }
}

/** The LiveKit client when the native modules are present, otherwise an explanation. */
export function createVoiceClient(): VoiceClient {
  try {
    const native = require("@livekit/react-native") as LiveKitNative;
    const client = require("livekit-client") as LiveKitClient;
    if (!globalsRegistered) {
      native.registerGlobals();
      globalsRegistered = true;
    }
    return new LiveKitVoiceClient(native, client);
  } catch {
    return new UnavailableVoiceClient();
  }
}
/* eslint-enable @typescript-eslint/no-require-imports */
