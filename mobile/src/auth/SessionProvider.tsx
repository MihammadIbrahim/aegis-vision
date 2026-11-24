import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { configureApiClient, type SessionEnd } from "../api/client";
import type { GuardDashboardKind, GuardTokenResponse, OperationalMode } from "../api/types";
import { secureDelete, secureGet, secureSet } from "../lib/secure";
import { SECURE_KEYS } from "../lib/storageKeys";

interface StoredSession {
  operational_mode: OperationalMode;
  dashboard: GuardDashboardKind;
  expires_at: string;
}

export type SessionState =
  | { status: "loading" }
  | { status: "signedOut"; notice: SessionNotice | null }
  | { status: "signedIn"; token: string; mode: OperationalMode; dashboard: GuardDashboardKind; expiresAt: string };

export interface SessionNotice {
  code: string;
  title: string;
  message: string;
}

/** The explanation shown on the sign-in screen after a forced sign-out. */
export function noticeFor(end: SessionEnd): SessionNotice {
  switch (end.code) {
    case "ASSIGNMENT_CHANGED":
      return {
        code: end.code,
        title: "Your assignment changed",
        message:
          "An Admin or Supervisor changed your post (Standby ↔ Stationed, or a new location). " +
          "Sign in again and choose the operational mode of your current assignment.",
      };
    case "ORGANIZATION_INACTIVE":
      return {
        code: end.code,
        title: "Organization not active",
        message: "Your organization is inactive or suspended, so the Guard app cannot be used. Contact your Admin.",
      };
    case "PASSWORD_CHANGED":
      return { code: end.code, title: "Password changed", message: "Sign in again with your new password." };
    case "SIGNED_OUT":
      return { code: end.code, title: "Signed out", message: "You signed out of the Guard app." };
    default:
      return {
        code: end.code,
        title: "Session ended",
        message: "Your session expired or is no longer valid. Please sign in again.",
      };
  }
}

type SignOutListener = (end: SessionEnd) => void | Promise<void>;

interface SessionContextValue {
  state: SessionState;
  signIn: (response: GuardTokenResponse) => Promise<void>;
  /** Stops alarms and calls (listeners), clears the encrypted session and cached data, returns to sign-in. */
  signOut: (end?: SessionEnd) => Promise<void>;
  /** Register work to do before the session is cleared (stop the alarm, leave a call, forget the push token). */
  onSignOut: (listener: SignOutListener) => () => void;
  clearNotice: () => void;
  token: () => string | null;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<SessionState>({ status: "loading" });
  const tokenRef = useRef<string | null>(null);
  const listeners = useRef(new Set<SignOutListener>());
  const signingOut = useRef(false);

  const signOut = useCallback(
    async (end: SessionEnd = { code: "SIGNED_OUT", message: "Signed out." }) => {
      if (signingOut.current) return;
      signingOut.current = true;
      try {
        for (const listener of Array.from(listeners.current)) {
          try {
            await listener(end);
          } catch {
            // Clean-up is best effort; the session is cleared regardless.
          }
        }
        tokenRef.current = null;
        await Promise.allSettled([secureDelete(SECURE_KEYS.accessToken), secureDelete(SECURE_KEYS.session)]);
        queryClient.clear();
        setState({ status: "signedOut", notice: noticeFor(end) });
      } finally {
        signingOut.current = false;
      }
    },
    [queryClient],
  );

  // The API client signs out on 401 / ORGANIZATION_INACTIVE from any request.
  useEffect(() => {
    configureApiClient({
      getToken: () => tokenRef.current,
      onSessionEnd: (end) => {
        if (tokenRef.current) void signOut(end);
      },
    });
  }, [signOut]);

  // Restore a stored session at start-up (expired ones are discarded).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [token, session] = await Promise.all([
        secureGet<string>(SECURE_KEYS.accessToken),
        secureGet<StoredSession>(SECURE_KEYS.session),
      ]);
      if (cancelled) return;
      if (token && session && new Date(session.expires_at).getTime() > Date.now()) {
        tokenRef.current = token;
        setState({
          status: "signedIn",
          token,
          mode: session.operational_mode,
          dashboard: session.dashboard,
          expiresAt: session.expires_at,
        });
      } else {
        await Promise.allSettled([secureDelete(SECURE_KEYS.accessToken), secureDelete(SECURE_KEYS.session)]);
        if (!cancelled) setState({ status: "signedOut", notice: null });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (response: GuardTokenResponse) => {
    const expiresAt = new Date(Date.now() + response.expires_in * 1000).toISOString();
    const stored: StoredSession = {
      operational_mode: response.operational_mode,
      dashboard: response.dashboard,
      expires_at: expiresAt,
    };
    await secureSet(SECURE_KEYS.accessToken, response.access_token);
    await secureSet(SECURE_KEYS.session, stored);
    tokenRef.current = response.access_token;
    setState({
      status: "signedIn",
      token: response.access_token,
      mode: response.operational_mode,
      dashboard: response.dashboard,
      expiresAt,
    });
  }, []);

  // Tokens have no refresh: when one expires the Guard signs in again (JWT_ACCESS_TOKEN_EXPIRE_MINUTES).
  useEffect(() => {
    if (state.status !== "signedIn") return;
    const remaining = new Date(state.expiresAt).getTime() - Date.now();
    const timer = setTimeout(
      () => void signOut({ code: "SESSION_EXPIRED", message: "Session expired." }),
      Math.max(0, Math.min(remaining, 2_147_000_000)),
    );
    return () => clearTimeout(timer);
  }, [state, signOut]);

  const onSignOut = useCallback((listener: SignOutListener) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);
  const clearNotice = useCallback(
    () => setState((s) => (s.status === "signedOut" ? { status: "signedOut", notice: null } : s)),
    [],
  );
  const token = useCallback(() => tokenRef.current, []);

  const value = useMemo<SessionContextValue>(
    () => ({ state, signIn, signOut, onSignOut, clearNotice, token }),
    [state, signIn, signOut, onSignOut, clearNotice, token],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}

/** The signed-in session; only for screens behind the (app) guard. */
export function useSignedIn() {
  const { state } = useSession();
  if (state.status !== "signedIn") throw new Error("Not signed in");
  return state;
}
