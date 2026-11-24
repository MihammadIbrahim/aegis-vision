import {
  ChevronDown,
  ChevronUp,
  Headphones,
  HeadphoneOff,
  Mic,
  MicOff,
  Phone,
  PhoneCall,
  PhoneIncoming,
  PhoneOff,
  Volume2,
} from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { Link } from "react-router-dom";

import { Button } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useLiveRefresh } from "../../../realtime/RealtimeProvider";
import { callsApi, ROLE_LABEL, targetLink } from "../api";
import { useCalls, useOptionalCalls, type CallValue } from "../CallProvider";
import type { CallSession, CallTarget } from "../types";

function duration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function scopeLabel(call: CallSession): string {
  return call.scope === "ALERT" ? "Alert call" : "Case call";
}

export function RoleBadge({ role }: { role: string }) {
  return <span className={`role-badge role-${role.toLowerCase()}`}>{ROLE_LABEL[role] ?? role}</span>;
}

/** A ringing invitation, shown on every page (the alert or case it is about, who is calling). */
export function IncomingCallBanner() {
  const calls = useCalls();
  const call = calls.incoming;
  if (!call) return null;
  return (
    <div className="incoming-call" role="alertdialog" aria-labelledby="incoming-call-title" aria-describedby="incoming-call-what" data-testid="incoming-call">
      <span className="incoming-call-icon" aria-hidden="true">
        <PhoneIncoming className="icon" />
      </span>
      <div className="incoming-call-text">
        <p id="incoming-call-title" className="incoming-call-title">
          Incoming {scopeLabel(call).toLowerCase()} from {call.started_by_name ?? "someone"} <RoleBadge role={call.started_by_role} />
        </p>
        <p id="incoming-call-what" className="incoming-call-what">
          <Link to={targetLink(call)}>{call.target_title || (call.scope === "ALERT" ? "Alert" : "Case")}</Link>
          {call.target_location ? ` · ${call.target_location}` : ""} · {call.joined_count} in the call
        </p>
        {calls.ringBlocked && <p className="incoming-call-hint">Ringing is silent until you click somewhere on the page (browser sound rules).</p>}
        {!calls.voiceAvailable && <p className="incoming-call-hint">{calls.voiceUnavailableReason}</p>}
      </div>
      <div className="incoming-call-actions">
        <Button variant="danger" size="sm" icon={PhoneOff} onClick={() => void calls.decline(call)} data-testid="incoming-decline">
          Decline
        </Button>
        <Button size="sm" icon={Phone} loading={calls.busy} disabled={!calls.voiceAvailable} onClick={() => void calls.accept(call)} data-testid="incoming-join">
          Join call
        </Button>
      </div>
    </div>
  );
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

/** Publishes the dock's height as --call-dock-space, so the page can be scrolled clear of it (AppShell pads the
 * main content while a call or call notice is shown). */
function useDockSpace(ref: RefObject<HTMLElement | null>, shown: string) {
  useEffect(() => {
    const element = ref.current;
    const root = document.documentElement;
    if (!element) return;
    const measure = () => root.style.setProperty("--call-dock-space", `${Math.ceil(element.getBoundingClientRect().height)}px`);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => {
      observer?.disconnect();
      root.style.removeProperty("--call-dock-space");
    };
  }, [ref, shown]);
}

/** The active call: a docked bar that never blocks the page, expandable to the participant panel. */
export function CallDock() {
  const calls = useCalls();
  const [expanded, setExpanded] = useState(true);
  const current = calls.current;
  const now = useNow(current !== null);
  const ref = useRef<HTMLElement | null>(null);
  useDockSpace(ref, current ? `call-${expanded}` : calls.notice ? "notice" : "none");
  if (!current) {
    return calls.notice ? (
      <div ref={ref as RefObject<HTMLDivElement | null>} className="call-dock is-notice" role="status" data-testid="call-notice">
        <p>{calls.notice}</p>
        <Button variant="ghost" size="sm" onClick={calls.clearNotice}>
          Dismiss
        </Button>
      </div>
    ) : null;
  }
  const call = current.call;
  const status =
    current.phase === "connecting" ? "Connecting…" : current.phase === "reconnecting" ? "Reconnecting…" : duration(now - current.joinedAt);
  const people = call.participants.filter((p) => p.state === "JOINED" || p.state === "INVITED");
  return (
    <section ref={ref} className={`call-dock${expanded ? " is-expanded" : ""}`} aria-label="Active call" data-testid="call-dock">
      <div className="call-dock-bar">
        <span className="call-dock-icon" aria-hidden="true">
          <PhoneCall className="icon" />
        </span>
        <span className="call-dock-title">
          <Link to={targetLink(call)}>{call.target_title || scopeLabel(call)}</Link>
          <span className="call-dock-status" data-testid="call-timer">
            {status} · {current.connected.length + 1} connected
          </span>
        </span>
        <button
          type="button"
          className={`call-control${current.muted ? " is-on" : ""}`}
          aria-pressed={current.muted}
          aria-label={current.muted ? "Unmute microphone" : "Mute microphone"}
          title={current.muted ? "Unmute microphone" : "Mute microphone"}
          onClick={() => void calls.toggleMute()}
          data-testid="call-mute"
        >
          {current.muted ? <MicOff className="icon" aria-hidden="true" /> : <Mic className="icon" aria-hidden="true" />}
        </button>
        <button
          type="button"
          className={`call-control${current.deafened ? " is-on" : ""}`}
          aria-pressed={current.deafened}
          aria-label={current.deafened ? "Hear the call again" : "Mute the call audio (deafen)"}
          title={current.deafened ? "Hear the call again" : "Mute the call audio (deafen)"}
          onClick={calls.toggleDeafen}
          data-testid="call-deafen"
        >
          {current.deafened ? <HeadphoneOff className="icon" aria-hidden="true" /> : <Headphones className="icon" aria-hidden="true" />}
        </button>
        <button
          type="button"
          className="call-control is-leave"
          aria-label="Leave call"
          title="Leave call"
          disabled={calls.busy}
          onClick={() => void calls.leave()}
          data-testid="call-leave"
        >
          <PhoneOff className="icon" aria-hidden="true" />
        </button>
        <button
          type="button"
          className="call-control"
          aria-expanded={expanded}
          aria-label={expanded ? "Hide participants" : "Show participants"}
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? <ChevronDown className="icon" aria-hidden="true" /> : <ChevronUp className="icon" aria-hidden="true" />}
        </button>
      </div>
      {expanded && (
        <div className="call-dock-panel">
          {call.target_location && <p className="muted small m-0">{call.target_location}</p>}
          <ul className="plain call-people" aria-label="Participants">
            {people.map((p) => {
              const connected = p.is_me ? current.phase === "active" : current.connected.includes(p.user_id);
              const speaking = current.speaking.includes(p.user_id);
              return (
                <li key={p.user_id} className={`call-person${speaking ? " is-speaking" : ""}`}>
                  <span className={`speaking-dot${speaking ? " is-on" : ""}`} aria-hidden="true" />
                  <span className="call-person-name">
                    {p.full_name}
                    {p.is_me ? " (you)" : ""}
                  </span>
                  <RoleBadge role={p.role} />
                  <span className="muted small">
                    {speaking ? "Speaking" : connected ? "Connected" : p.state === "INVITED" ? "Ringing" : "Joining…"}
                  </span>
                </li>
              );
            })}
          </ul>
          {current.microphoneError && <p className="notice warning">{current.microphoneError}</p>}
          {calls.audioBlocked && (
            <p className="notice info">
              The browser paused the call audio.{" "}
              <Button size="sm" variant="secondary" icon={Volume2} onClick={() => void calls.enableAudio()}>
                Play call audio
              </Button>
            </p>
          )}
          {calls.notice && <p className="notice error">{calls.notice}</p>}
          {call.can_end && (
            <Button variant="danger" size="sm" icon={PhoneOff} disabled={calls.busy} onClick={() => void calls.endForEveryone()} data-testid="call-end-all">
              End call for everyone
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

/** "Start voice call" / "Join active call" for one alert or case, with a banner while a call is going on. Renders
 * nothing (and asks for nothing) outside the signed-in shell's CallProvider. */
export function CallControls({ target, closed }: { target: CallTarget; closed: boolean }) {
  const calls = useOptionalCalls();
  return calls ? <CallControlsCard calls={calls} target={target} closed={closed} /> : null;
}

function CallControlsCard({ calls, target, closed }: { calls: CallValue; target: CallTarget; closed: boolean }) {
  const active = useAsync(() => callsApi.active(target), [target.scope, target.id]);
  useLiveRefresh(
    active.reload,
    (e) =>
      (e.event === "call.updated" || e.event === "call.invited") &&
      (target.scope === "ALERT" ? e.alert_id === target.id : e.incident_id === target.id),
    { fallbackMs: 15_000 },
  );
  const ongoing = active.data?.call ?? null;
  const inThisCall = ongoing !== null && calls.current?.call.id === ongoing.id;
  const notConfigured = active.data !== undefined && !active.data.voice_calls_available;
  const label = target.scope === "ALERT" ? "alert" : "case";
  return (
    <section className="card call-controls" aria-labelledby={`call-${target.id}`} data-testid="call-controls">
      <div className="card-head">
        <h2 id={`call-${target.id}`}>Voice call</h2>
        {inThisCall ? (
          <span className="badge tone-resolved">You are in this call</span>
        ) : ongoing ? (
          <Button icon={Phone} loading={calls.busy} disabled={!calls.voiceAvailable} onClick={() => void calls.accept(ongoing)} data-testid="call-join">
            Join active call
          </Button>
        ) : (
          <Button
            icon={PhoneCall}
            loading={calls.busy}
            disabled={closed || notConfigured || !calls.voiceAvailable}
            onClick={() => void calls.start(target)}
            data-testid="call-start"
          >
            Start voice call
          </Button>
        )}
      </div>
      {ongoing && (
        <p className="notice success" data-testid="active-call-banner">
          A call is going on: {ongoing.joined_count} in the call
          {ongoing.participants.some((p) => p.state === "JOINED")
            ? ` (${ongoing.participants
                .filter((p) => p.state === "JOINED")
                .map((p) => `${p.full_name}, ${ROLE_LABEL[p.role] ?? p.role}`)
                .join("; ")})`
            : ""}
          .
        </p>
      )}
      {!ongoing && closed && <p className="muted small m-0">This {label} is closed, so no call can be started.</p>}
      {notConfigured && (
        <p className="notice warning" data-testid="voice-not-configured">
          Voice calls are not configured on this server (VOICE_CALL_PROVIDER=none). The chat still works.
        </p>
      )}
      {!calls.voiceAvailable && !notConfigured && <p className="notice warning">{calls.voiceUnavailableReason}</p>}
      {!ongoing && !closed && !notConfigured && calls.voiceAvailable && (
        <p className="muted small m-0">
          Rings this campus's Admins and Supervisors and the {label}'s Guards. Audio only; only people of this {label} can
          join.
        </p>
      )}
    </section>
  );
}
