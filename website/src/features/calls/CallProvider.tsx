/**
 * In-app voice calls for Admins and Supervisors (decision D-026), the web side of D-025: start or join a call about an
 * alert or a case, ring for invitations (WebSocket `call.invited`, with GET /calls polling as the fallback), mute,
 * deafen, see who is speaking, leave, and end for everyone. The same rules and states as the Guard app.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { useRealtime, useRealtimeEvents } from "../../realtime/RealtimeProvider";
import { callsApi } from "./api";
import { silentRinger, WebAudioRinger, type Ringer } from "./ringer";
import type { CallJoin, CallSession, CallTarget } from "./types";
import { createVoiceClient, microphoneMessage, type VoiceClient } from "./voice";

export type CallPhase = "connecting" | "active" | "reconnecting";

export interface CurrentCall {
  call: CallSession;
  phase: CallPhase;
  muted: boolean;
  deafened: boolean;
  /** Account IDs connected to the audio room right now, and those speaking. */
  connected: string[];
  speaking: string[];
  joinedAt: number;
  microphoneError: string | null;
}

export interface CallValue {
  voiceAvailable: boolean;
  voiceUnavailableReason: string | null;
  current: CurrentCall | null;
  incoming: CallSession | null;
  busy: boolean;
  notice: string | null;
  audioBlocked: boolean;
  ringBlocked: boolean;
  clearNotice: () => void;
  start: (target: CallTarget) => Promise<void>;
  accept: (call: CallSession) => Promise<void>;
  decline: (call: CallSession) => Promise<void>;
  leave: () => Promise<void>;
  endForEveryone: () => Promise<void>;
  toggleMute: () => Promise<void>;
  toggleDeafen: () => void;
  enableAudio: () => Promise<void>;
}

export const NOT_CONFIGURED_NOTICE =
  "In-app voice calls are not configured on this server yet (VOICE_CALL_PROVIDER=none, no LiveKit keys). Use the chat for now.";

const CallContext = createContext<CallValue | null>(null);

const POLL_DOWN_MS = 5_000;
const POLL_LIVE_MS = 30_000;

function describe(error: unknown): string {
  if (error instanceof ApiError && error.code === "VOICE_CALL_NOT_CONFIGURED") return NOT_CONFIGURED_NOTICE;
  return error instanceof Error ? error.message : String(error);
}

export function CallProvider({
  children,
  voice: injectedVoice,
  ringer: injectedRinger,
}: {
  children: ReactNode;
  voice?: VoiceClient;
  ringer?: Ringer;
}) {
  const [voice] = useState<VoiceClient>(() => injectedVoice ?? createVoiceClient());
  const [ringer] = useState<Ringer>(() => injectedRinger ?? (typeof window === "undefined" ? silentRinger : new WebAudioRinger()));
  const { beforeSignOut } = useAuth();
  const { live } = useRealtime();
  const [current, setCurrent] = useState<CurrentCall | null>(null);
  const [mine, setMine] = useState<CallSession[]>([]);
  const [declined, setDeclined] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [ringBlocked, setRingBlocked] = useState(false);
  const currentRef = useRef<CurrentCall | null>(null);

  const update = useCallback((next: CurrentCall | null | ((c: CurrentCall | null) => CurrentCall | null)) => {
    setCurrent((previous) => {
      const value = typeof next === "function" ? next(previous) : next;
      currentRef.current = value;
      return value;
    });
  }, []);

  const loadMine = useCallback(() => {
    callsApi
      .mine()
      .then(setMine)
      .catch(() => undefined);
  }, []);

  const incoming = useMemo(() => {
    if (current) return null;
    return mine.find((c) => c.status === "ACTIVE" && c.my_state === "INVITED" && !declined.has(c.id)) ?? null;
  }, [mine, current, declined]);

  const hangUp = useCallback(
    async (message: string | null) => {
      update(null);
      setAudioBlocked(false);
      if (message) setNotice(message);
      await voice.disconnect().catch(() => undefined);
    },
    [voice, update],
  );

  const connect = useCallback(
    async (joined: CallJoin) => {
      update({
        call: joined.call,
        phase: "connecting",
        muted: false,
        deafened: false,
        connected: [],
        speaking: [],
        joinedAt: Date.now(),
        microphoneError: null,
      });
      try {
        const result = await voice.connect(joined.connection.url, joined.connection.token, {
          onParticipants: (connected) => update((c) => (c ? { ...c, connected } : c)),
          onSpeakers: (speaking) => update((c) => (c ? { ...c, speaking } : c)),
          onReconnecting: () => update((c) => (c ? { ...c, phase: "reconnecting" } : c)),
          onReconnected: () => update((c) => (c ? { ...c, phase: "active" } : c)),
          onAudioBlocked: setAudioBlocked,
          onDisconnected: (reason) => {
            const active = currentRef.current;
            if (!active) return;
            if (reason === "elsewhere") {
              // Still in the call on the server, from the other tab or device: do not leave it.
              void hangUp("You joined this call from another tab or device, so it continues there.");
              return;
            }
            void callsApi.leave(active.call.id).catch(() => undefined);
            void hangUp("The call audio disconnected.");
          },
        });
        update((c) =>
          c ? { ...c, phase: "active", muted: !result.microphone, microphoneError: result.microphoneError } : c,
        );
      } catch (error) {
        // Never stay "in" a call on the server without audio.
        await callsApi.leave(joined.call.id).catch(() => undefined);
        await hangUp(`The call audio could not connect: ${describe(error)}`);
      }
      loadMine();
    },
    [voice, update, hangUp, loadMine],
  );

  const run = useCallback(async (work: () => Promise<void>) => {
    setBusy(true);
    setNotice(null);
    try {
      await work();
    } catch (error) {
      setNotice(describe(error));
    } finally {
      setBusy(false);
    }
  }, []);

  const start = useCallback(
    (target: CallTarget) =>
      run(async () => {
        if (!voice.available) throw new Error(voice.unavailableReason ?? "Voice calls are not available.");
        if (currentRef.current) throw new Error("You are already in a call. Leave it first.");
        await connect(await callsApi.start(target));
      }),
    [run, voice, connect],
  );

  const accept = useCallback(
    (call: CallSession) =>
      run(async () => {
        if (!voice.available) throw new Error(voice.unavailableReason ?? "Voice calls are not available.");
        if (currentRef.current?.call.id === call.id) return;
        if (currentRef.current) throw new Error("You are already in a call. Leave it first.");
        await connect(await callsApi.join(call.id));
      }),
    [run, voice, connect],
  );

  const decline = useCallback(
    (call: CallSession) =>
      run(async () => {
        setDeclined((set) => new Set(set).add(call.id));
        await callsApi.decline(call.id);
        loadMine();
      }),
    [run, loadMine],
  );

  const leave = useCallback(
    () =>
      run(async () => {
        const active = currentRef.current;
        if (!active) return;
        try {
          await callsApi.leave(active.call.id);
        } finally {
          await hangUp(null);
        }
      }),
    [run, hangUp],
  );

  const endForEveryone = useCallback(
    () =>
      run(async () => {
        const active = currentRef.current;
        if (!active) return;
        await callsApi.end(active.call.id);
        await hangUp("You ended the call for everyone.");
      }),
    [run, hangUp],
  );

  const toggleMute = useCallback(async () => {
    const active = currentRef.current;
    if (!active) return;
    try {
      await voice.setMicrophoneEnabled(active.muted);
      update((c) => (c ? { ...c, muted: !c.muted, microphoneError: null } : c));
    } catch (error) {
      update((c) => (c ? { ...c, muted: true, microphoneError: microphoneMessage(error) } : c));
    }
  }, [voice, update]);

  const toggleDeafen = useCallback(() => {
    const active = currentRef.current;
    if (!active) return;
    voice.setDeafened(!active.deafened);
    update((c) => (c ? { ...c, deafened: !c.deafened } : c));
  }, [voice, update]);

  const enableAudio = useCallback(async () => {
    await voice.enableAudio().catch(() => undefined);
    setAudioBlocked(false);
  }, [voice]);

  const refreshCurrent = useCallback(() => {
    const active = currentRef.current;
    if (!active) return;
    callsApi
      .get(active.call.id)
      .then((call) => {
        if (call.status === "ENDED") {
          void hangUp(
            call.end_reason === "TARGET_CLOSED"
              ? "The call ended because its alert or case was closed."
              : call.end_reason === "ENDED_BY_USER"
                ? "The call was ended for everyone."
                : "The call ended.",
          );
        } else {
          update((c) => (c && c.call.id === call.id ? { ...c, call } : c));
        }
      })
      .catch(() => undefined);
  }, [hangUp, update]);

  // Signals: invitations and changes ring or refresh at once.
  useRealtimeEvents((event) => {
    if (event.event === "call.invited" || event.event === "call.updated" || event.kind === "RESYNC") loadMine();
    if (event.event === "call.updated" && event.call_id && event.call_id === currentRef.current?.call.id) refreshCurrent();
  });

  // Polling fallback (fast while the WebSocket is down) and a slow safety net while it is up.
  useEffect(() => {
    loadMine();
    const timer = setInterval(() => {
      loadMine();
      refreshCurrent();
    }, live ? POLL_LIVE_MS : POLL_DOWN_MS);
    return () => clearInterval(timer);
  }, [live, loadMine, refreshCurrent]);

  // Ring while an invitation is waiting.
  useEffect(() => {
    if (!incoming) {
      ringer.stop();
      setRingBlocked(false);
      return;
    }
    void ringer.start().then((ringing) => setRingBlocked(!ringing));
    return () => ringer.stop();
  }, [incoming, ringer]);

  // Signing out (or the session ending): leave the call while the token still exists, and drop the audio.
  useEffect(
    () =>
      beforeSignOut(() => {
        const active = currentRef.current;
        if (active) void callsApi.leave(active.call.id).catch(() => undefined);
        void hangUp(null);
        ringer.stop();
      }),
    [beforeSignOut, hangUp, ringer],
  );
  useEffect(
    () => () => {
      ringer.stop();
      void voice.disconnect().catch(() => undefined);
    },
    [ringer, voice],
  );

  const value = useMemo<CallValue>(
    () => ({
      voiceAvailable: voice.available,
      voiceUnavailableReason: voice.unavailableReason,
      current,
      incoming,
      busy,
      notice,
      audioBlocked,
      ringBlocked,
      clearNotice: () => setNotice(null),
      start,
      accept,
      decline,
      leave,
      endForEveryone,
      toggleMute,
      toggleDeafen,
      enableAudio,
    }),
    [voice, current, incoming, busy, notice, audioBlocked, ringBlocked, start, accept, decline, leave, endForEveryone, toggleMute, toggleDeafen, enableAudio],
  );
  return <CallContext.Provider value={value}>{children}</CallContext.Provider>;
}

export function useCalls(): CallValue {
  const value = useContext(CallContext);
  if (!value) throw new Error("useCalls must be used inside CallProvider");
  return value;
}

/** For pages rendered outside the signed-in shell in tests: calls are simply unavailable. */
export function useOptionalCalls(): CallValue | null {
  return useContext(CallContext);
}
