/**
 * In-app voice calls about an alert or a case (decision D-025): start or join, ring for invitations (WebSocket
 * `call.invited`, with polling of GET /calls as the fallback), mute, speakerphone, leave, end for everyone.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, errorMessage } from "../api/client";
import { callsApi, type CallTargetRef } from "../api/endpoints";
import type { CallJoinResponse, CallSessionResponse } from "../api/types";
import { useSession } from "../auth/SessionProvider";
import { qk } from "../lib/queryKeys";
import { useRealtime } from "../realtime/RealtimeProvider";
import { createVoiceClient, type VoiceClient } from "./voiceClient";

export type CallPhase = "connecting" | "active" | "reconnecting";

export interface CurrentCall {
  call: CallSessionResponse;
  phase: CallPhase;
  muted: boolean;
  speaker: boolean;
  /** Account IDs of the people connected to the audio room right now. */
  connected: string[];
  joinedAt: number;
}

interface CallValue {
  voiceAvailable: boolean;
  voiceUnavailableReason: string | null;
  current: CurrentCall | null;
  incoming: CallSessionResponse | null;
  busy: boolean;
  notice: string | null;
  expanded: boolean;
  setExpanded: (value: boolean) => void;
  clearNotice: () => void;
  start: (target: CallTargetRef) => Promise<void>;
  accept: (call: CallSessionResponse) => Promise<void>;
  decline: (call: CallSessionResponse) => Promise<void>;
  leave: () => Promise<void>;
  endForEveryone: () => Promise<void>;
  toggleMute: () => Promise<void>;
  toggleSpeaker: () => Promise<void>;
}

const CallContext = createContext<CallValue | null>(null);

export const NOT_CONFIGURED_NOTICE =
  "In-app voice calls are not configured on this server yet (no LiveKit keys). Use the chat for now.";

function describe(error: unknown): string {
  if (error instanceof ApiError && error.code === "VOICE_CALL_NOT_CONFIGURED") return NOT_CONFIGURED_NOTICE;
  return errorMessage(error);
}

export function CallProvider({ children, voice: injected }: { children: ReactNode; voice?: VoiceClient }) {
  const [voice] = useState<VoiceClient>(() => injected ?? createVoiceClient());
  const queryClient = useQueryClient();
  const { state, onSignOut } = useSession();
  const { pollMs, subscribe } = useRealtime();
  const [current, setCurrent] = useState<CurrentCall | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [declined, setDeclined] = useState<Set<string>>(new Set());
  const currentRef = useRef<CurrentCall | null>(null);
  useEffect(() => {
    currentRef.current = current;
  }, [current]);

  const mine = useQuery({
    queryKey: qk.myCalls,
    queryFn: callsApi.mine,
    enabled: state.status === "signedIn",
    refetchInterval: pollMs,
  });

  const incoming = useMemo(() => {
    if (current) return null;
    return (
      (mine.data ?? []).find((c) => c.status === "ACTIVE" && c.my_state === "INVITED" && !declined.has(c.id)) ?? null
    );
  }, [mine.data, current, declined]);

  const hangUpLocally = useCallback(
    async (message: string | null) => {
      currentRef.current = null;
      setCurrent(null);
      if (message) setNotice(message);
      await voice.disconnect().catch(() => undefined);
    },
    [voice],
  );

  const connect = useCallback(
    async (joined: CallJoinResponse) => {
      const fresh: CurrentCall = {
        call: joined.call,
        phase: "connecting",
        muted: false,
        speaker: false,
        connected: [],
        joinedAt: Date.now(),
      };
      currentRef.current = fresh;
      setCurrent(fresh);
      setExpanded(true);
      try {
        await voice.connect(joined.connection.url, joined.connection.token, {
          onParticipantsChanged: (connected) =>
            setCurrent((c) => (c ? { ...c, connected, phase: c.phase === "connecting" ? "active" : c.phase } : c)),
          onReconnecting: () => setCurrent((c) => (c ? { ...c, phase: "reconnecting" } : c)),
          onReconnected: () => setCurrent((c) => (c ? { ...c, phase: "active" } : c)),
          onDisconnected: () => {
            const active = currentRef.current;
            if (!active) return;
            void callsApi.leave(active.call.id).catch(() => undefined);
            void hangUpLocally("The call audio disconnected.");
          },
        });
        setCurrent((c) => (c ? { ...c, phase: "active" } : c));
      } catch (failure) {
        // Never stay "in" a call on the server without audio.
        await callsApi.leave(joined.call.id).catch(() => undefined);
        await hangUpLocally(`Could not connect the call audio: ${errorMessage(failure)}`);
      }
      void queryClient.invalidateQueries({ queryKey: qk.myCalls });
    },
    [voice, hangUpLocally, queryClient],
  );

  const run = useCallback(async (work: () => Promise<void>) => {
    setBusy(true);
    setNotice(null);
    try {
      await work();
    } catch (failure) {
      setNotice(describe(failure));
    } finally {
      setBusy(false);
    }
  }, []);

  const start = useCallback(
    (target: CallTargetRef) =>
      run(async () => {
        if (!voice.available) throw new Error(voice.unavailableReason ?? "Voice calls are not available.");
        if (currentRef.current) throw new Error("You are already in a call. Leave it first.");
        await connect(await callsApi.start(target));
      }),
    [run, voice, connect],
  );

  const accept = useCallback(
    (call: CallSessionResponse) =>
      run(async () => {
        if (!voice.available) throw new Error(voice.unavailableReason ?? "Voice calls are not available.");
        await connect(await callsApi.join(call.id));
      }),
    [run, voice, connect],
  );

  const decline = useCallback(
    (call: CallSessionResponse) =>
      run(async () => {
        setDeclined((set) => new Set(set).add(call.id));
        await callsApi.decline(call.id);
        void queryClient.invalidateQueries({ queryKey: qk.myCalls });
      }),
    [run, queryClient],
  );

  const leave = useCallback(
    () =>
      run(async () => {
        const active = currentRef.current;
        if (!active) return;
        try {
          await callsApi.leave(active.call.id);
        } finally {
          await hangUpLocally(null);
        }
      }),
    [run, hangUpLocally],
  );

  const endForEveryone = useCallback(
    () =>
      run(async () => {
        const active = currentRef.current;
        if (!active) return;
        await callsApi.end(active.call.id);
        await hangUpLocally("Call ended for everyone.");
      }),
    [run, hangUpLocally],
  );

  const toggleMute = useCallback(async () => {
    const active = currentRef.current;
    if (!active) return;
    await voice.setMicrophoneEnabled(active.muted);
    setCurrent((c) => (c ? { ...c, muted: !c.muted } : c));
  }, [voice]);

  const toggleSpeaker = useCallback(async () => {
    const active = currentRef.current;
    if (!active) return;
    await voice.setSpeakerphone(!active.speaker);
    setCurrent((c) => (c ? { ...c, speaker: !c.speaker } : c));
  }, [voice]);

  // Keep the current call's participants fresh; leave when the server ended it (alert or case closed, or ended).
  useEffect(
    () =>
      subscribe((event) => {
        const active = currentRef.current;
        if (!active || event.call_id !== active.call.id) return;
        void callsApi
          .get(active.call.id)
          .then((call) => {
            if (call.status === "ENDED") {
              void hangUpLocally(
                call.end_reason === "TARGET_CLOSED" ? "The call ended because the alert or case was closed." : "The call ended.",
              );
            } else {
              setCurrent((c) => (c && c.call.id === call.id ? { ...c, call } : c));
            }
          })
          .catch(() => undefined);
      }),
    [subscribe, hangUpLocally],
  );

  // Polling fallback for the same.
  useEffect(() => {
    if (!current) return;
    const timer = setInterval(() => {
      const active = currentRef.current;
      if (!active) return;
      void callsApi
        .get(active.call.id)
        .then((call) => {
          if (call.status === "ENDED") void hangUpLocally("The call ended.");
          else setCurrent((c) => (c && c.call.id === call.id ? { ...c, call } : c));
        })
        .catch(() => undefined);
    }, pollMs);
    return () => clearInterval(timer);
  }, [current?.call.id, pollMs, hangUpLocally]); // eslint-disable-line react-hooks/exhaustive-deps

  // Session ended: leave the call and drop the audio.
  useEffect(
    () =>
      onSignOut(async () => {
        const active = currentRef.current;
        if (active) await callsApi.leave(active.call.id).catch(() => undefined);
        await hangUpLocally(null);
        setDeclined(new Set());
      }),
    [onSignOut, hangUpLocally],
  );

  const value = useMemo<CallValue>(
    () => ({
      voiceAvailable: voice.available,
      voiceUnavailableReason: voice.unavailableReason,
      current,
      incoming,
      busy,
      notice,
      expanded,
      setExpanded,
      clearNotice: () => setNotice(null),
      start,
      accept,
      decline,
      leave,
      endForEveryone,
      toggleMute,
      toggleSpeaker,
    }),
    [voice, current, incoming, busy, notice, expanded, start, accept, decline, leave, endForEveryone, toggleMute, toggleSpeaker],
  );

  return <CallContext.Provider value={value}>{children}</CallContext.Provider>;
}

export function useCalls(): CallValue {
  const value = useContext(CallContext);
  if (!value) throw new Error("useCalls must be used inside CallProvider");
  return value;
}
