/**
 * Browser audio for in-app calls (decisions D-025, D-026) behind a small interface, so tests, headless browsers and
 * browsers without WebRTC degrade with an explanation instead of crashing. LiveKit (`livekit-client`) is loaded only
 * when a call starts. Audio flows between the browser and the LiveKit server; the Core API only signs room tokens.
 */

export interface VoiceEvents {
  /** Account IDs of the other people connected to the room. */
  onParticipants: (identities: string[]) => void;
  /** Account IDs of whoever is speaking now (may include the caller's own ID). */
  onSpeakers: (identities: string[]) => void;
  /** The audio room dropped this browser: "elsewhere" when the same account joined the call from another tab or
   * device (LiveKit allows one connection per identity), "lost" otherwise. */
  onDisconnected: (reason: "elsewhere" | "lost") => void;
  onReconnecting?: () => void;
  onReconnected?: () => void;
  /** The browser blocked audio playback until the user clicks (autoplay rules). */
  onAudioBlocked?: (blocked: boolean) => void;
}

export interface ConnectResult {
  /** False when the microphone could not be used (permission denied, no device): the caller can still listen. */
  microphone: boolean;
  microphoneError: string | null;
}

export interface VoiceClient {
  readonly available: boolean;
  readonly unavailableReason: string | null;
  connect(url: string, token: string, events: VoiceEvents): Promise<ConnectResult>;
  disconnect(): Promise<void>;
  setMicrophoneEnabled(enabled: boolean): Promise<void>;
  /** Mutes every other participant for this browser only ("deafen"). */
  setDeafened(deafened: boolean): void;
  /** Starts audio after a click when the browser blocked it. */
  enableAudio(): Promise<void>;
}

/** Why this browser cannot make calls, or null when it can. */
export function browserVoiceSupport(env: {
  secure: boolean;
  hasPeerConnection: boolean;
  hasMediaDevices: boolean;
}): string | null {
  if (!env.secure) return "Voice calls need a secure (https) address or localhost: browsers only allow the microphone there.";
  if (!env.hasPeerConnection) return "This browser cannot make voice calls (no WebRTC support).";
  if (!env.hasMediaDevices) return "This browser gives no access to a microphone.";
  return null;
}

export function microphoneMessage(error: unknown): string {
  // getUserMedia rejects with a DOMException, which does not inherit from Error everywhere.
  const name = typeof error === "object" && error !== null && "name" in error ? String(error.name) : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return "Microphone permission was denied. Allow the microphone for this site to talk; you can still listen.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No microphone was found. You can still listen.";
  }
  return "The microphone could not be started. You can still listen.";
}

export class UnavailableVoiceClient implements VoiceClient {
  readonly available = false;
  constructor(readonly unavailableReason: string) {}
  async connect(): Promise<ConnectResult> {
    throw new Error(this.unavailableReason);
  }
  async disconnect(): Promise<void> {}
  async setMicrophoneEnabled(): Promise<void> {}
  setDeafened(): void {}
  async enableAudio(): Promise<void> {}
}

type LiveKit = typeof import("livekit-client");
type Room = InstanceType<LiveKit["Room"]>;

export class LiveKitVoiceClient implements VoiceClient {
  readonly available = true;
  readonly unavailableReason = null;
  private room: Room | null = null;
  private elements = new Set<HTMLMediaElement>();
  private deafened = false;

  async connect(url: string, token: string, events: VoiceEvents): Promise<ConnectResult> {
    const lk: LiveKit = await import("livekit-client");
    const room = new lk.Room({ adaptiveStream: false, dynacast: false });
    this.room = room;
    const others = () => Array.from(room.remoteParticipants.values()).map((p) => p.identity);
    room.on(lk.RoomEvent.ParticipantConnected, () => events.onParticipants(others()));
    room.on(lk.RoomEvent.ParticipantDisconnected, () => events.onParticipants(others()));
    room.on(lk.RoomEvent.ActiveSpeakersChanged, (speakers) => events.onSpeakers(speakers.map((p) => p.identity)));
    room.on(lk.RoomEvent.Reconnecting, () => events.onReconnecting?.());
    room.on(lk.RoomEvent.Reconnected, () => events.onReconnected?.());
    room.on(lk.RoomEvent.Disconnected, (reason) =>
      events.onDisconnected(reason === lk.DisconnectReason.DUPLICATE_IDENTITY ? "elsewhere" : "lost"),
    );
    room.on(lk.RoomEvent.AudioPlaybackStatusChanged, () => events.onAudioBlocked?.(!room.canPlaybackAudio));
    room.on(lk.RoomEvent.TrackSubscribed, (track) => {
      if (track.kind !== lk.Track.Kind.Audio) return;
      const element = track.attach();
      element.muted = this.deafened;
      element.style.display = "none";
      document.body.appendChild(element);
      this.elements.add(element);
    });
    room.on(lk.RoomEvent.TrackUnsubscribed, (track) => {
      for (const element of track.detach()) {
        this.elements.delete(element);
        element.remove();
      }
    });
    await room.connect(url, token);
    events.onParticipants(others());
    events.onAudioBlocked?.(!room.canPlaybackAudio);
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
      return { microphone: true, microphoneError: null };
    } catch (error) {
      return { microphone: false, microphoneError: microphoneMessage(error) };
    }
  }

  async disconnect(): Promise<void> {
    const room = this.room;
    this.room = null;
    for (const element of this.elements) element.remove();
    this.elements.clear();
    await room?.disconnect();
  }

  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    await this.room?.localParticipant.setMicrophoneEnabled(enabled);
  }

  setDeafened(deafened: boolean): void {
    this.deafened = deafened;
    for (const element of this.elements) element.muted = deafened;
  }

  async enableAudio(): Promise<void> {
    await this.room?.startAudio();
  }
}

/** The LiveKit client in a capable browser, otherwise an explanation. */
export function createVoiceClient(): VoiceClient {
  const reason = browserVoiceSupport({
    secure: typeof window !== "undefined" && window.isSecureContext !== false,
    hasPeerConnection: typeof RTCPeerConnection !== "undefined",
    hasMediaDevices: typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia),
  });
  return reason ? new UnavailableVoiceClient(reason) : new LiveKitVoiceClient();
}
