/* Fakes for the WebSocket, voice client and ringer used by the real-time and call tests. */
import type { SocketLike } from "../realtime/socket";
import type { ConnectResult, VoiceClient, VoiceEvents } from "../features/calls/voice";
import type { Ringer } from "../features/calls/ringer";

export class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: Array<Record<string, unknown>> = [];
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number; reason?: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  static latest(): FakeSocket | undefined {
    return FakeSocket.instances[FakeSocket.instances.length - 1];
  }
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }
  ready(): void {
    this.open();
    this.serverSend({ type: "ready", user_id: "u", role: "ADMIN", heartbeat_seconds: 25, token_expires_at: "x" });
  }
  serverSend(message: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  event(event: string, fields: Record<string, string> = {}): void {
    this.serverSend({ type: "event", event, at: new Date().toISOString(), ...fields });
  }
  serverClose(code: number): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }
  close(): void {
    this.readyState = 3;
  }
}

export class FakeVoice implements VoiceClient {
  connected: { url: string; token: string } | null = null;
  events: VoiceEvents | null = null;
  microphone = true;
  deafened = false;
  constructor(
    readonly available = true,
    readonly unavailableReason: string | null = null,
    private readonly result: ConnectResult = { microphone: true, microphoneError: null },
  ) {}
  async connect(url: string, token: string, events: VoiceEvents): Promise<ConnectResult> {
    this.connected = { url, token };
    this.events = events;
    this.microphone = this.result.microphone;
    return this.result;
  }
  async disconnect(): Promise<void> {
    this.connected = null;
  }
  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    this.microphone = enabled;
  }
  setDeafened(deafened: boolean): void {
    this.deafened = deafened;
  }
  async enableAudio(): Promise<void> {}
}

export class FakeRinger implements Ringer {
  ringing = false;
  constructor(private readonly allowed = true) {}
  async start(): Promise<boolean> {
    this.ringing = this.allowed;
    return this.allowed;
  }
  stop(): void {
    this.ringing = false;
  }
}
