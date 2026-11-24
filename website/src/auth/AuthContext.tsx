import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { getToken, onUnauthorized, setToken } from "../api/client";
import { auth } from "./api";
import type { Session } from "../api/types";

/** Why a session ended without the user signing out (shown on the sign-in page). */
export interface SignOutNotice {
  code: string;
  message: string;
}

export function noticeFor(code: string | undefined): SignOutNotice | null {
  switch (code) {
    case undefined:
    case "SIGNED_OUT":
      return null;
    case "ORGANIZATION_INACTIVE":
      return { code, message: "Your organization is inactive or suspended, so you were signed out. Contact the platform administrator." };
    case "SUPER_ADMIN_NOT_ALLOWED":
      return { code, message: "Platform Super Admin accounts cannot use the organization dashboard." };
    default:
      return { code, message: "Your session ended (it expired or your password changed). Please sign in again." };
  }
}

interface AuthState {
  session: Session | null;
  loading: boolean;
  signIn: (token: string) => Promise<Session>;
  /** ``code`` says why when the session ended by itself (e.g. ORGANIZATION_INACTIVE). */
  signOut: (code?: string) => void;
  notice: SignOutNotice | null;
  clearNotice: () => void;
  /** Work to do while the token still exists (e.g. leave an active call). Returns an unregister function. */
  beforeSignOut: (hook: () => void) => () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState<boolean>(() => getToken() !== null);
  const [notice, setNotice] = useState<SignOutNotice | null>(null);
  const hooks = useRef(new Set<() => void>());

  const signOut = useCallback((code?: string) => {
    // Clean-up first: requests sent here still carry the token (it is read when they start).
    for (const hook of Array.from(hooks.current)) {
      try {
        hook();
      } catch {
        // best effort
      }
    }
    setToken(null);
    setSession(null);
    setNotice(noticeFor(typeof code === "string" ? code : undefined));
  }, []);

  const beforeSignOut = useCallback((hook: () => void) => {
    hooks.current.add(hook);
    return () => {
      hooks.current.delete(hook);
    };
  }, []);
  const clearNotice = useCallback(() => setNotice(null), []);

  useEffect(() => {
    onUnauthorized(signOut);
    if (getToken()) {
      auth
        .me()
        .then(setSession)
        .catch(signOut)
        .finally(() => setLoading(false));
    }
    return () => onUnauthorized(null);
  }, [signOut]);

  const signIn = useCallback(async (token: string) => {
    setToken(token);
    const me = await auth.me();
    setSession(me);
    setNotice(null);
    return me;
  }, []);

  const value = useMemo(
    () => ({ session, loading, signIn, signOut, notice, clearNotice, beforeSignOut }),
    [session, loading, signIn, signOut, notice, clearNotice, beforeSignOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

/** For tests and stories: a fixed session. */
export function StaticAuthProvider({
  session,
  children,
  signOut,
  beforeSignOut,
}: {
  session: Session | null;
  children: ReactNode;
  /** Tests may observe sign-outs and the clean-up hooks registered before them. */
  signOut?: (code?: string) => void;
  beforeSignOut?: (hook: () => void) => () => void;
}) {
  const value = useMemo(
    () => ({
      session,
      loading: false,
      signIn: async () => session as Session,
      signOut: signOut ?? (() => undefined),
      notice: null,
      clearNotice: () => undefined,
      beforeSignOut: beforeSignOut ?? (() => () => undefined),
    }),
    [session, signOut, beforeSignOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
