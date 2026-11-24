import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { getToken, onUnauthorized, setToken } from "../api/client";
import { platform } from "../api/platform";
import type { LoginMethod, PlatformAdmin } from "../api/types";
import { loginMethodOf } from "./token";

export interface Session {
  admin: PlatformAdmin;
  loginMethod: LoginMethod;
}

interface AuthState {
  session: Session | null;
  loading: boolean;
  /** Store a new token (sign-in, or the fresh token a credential change returns) and reload /me. */
  adoptToken: (token: string) => Promise<Session>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

async function load(token: string): Promise<Session> {
  return { admin: await platform.me(), loginMethod: loginMethodOf(token) };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState<boolean>(() => getToken() !== null);

  const signOut = useCallback(() => {
    setToken(null);
    setSession(null);
  }, []);

  useEffect(() => {
    onUnauthorized(signOut);
    const token = getToken();
    if (token) {
      load(token)
        .then(setSession)
        .catch(signOut)
        .finally(() => setLoading(false));
    }
    return () => onUnauthorized(null);
  }, [signOut]);

  const adoptToken = useCallback(async (token: string) => {
    setToken(token);
    const next = await load(token);
    setSession(next);
    return next;
  }, []);

  const value = useMemo(() => ({ session, loading, adoptToken, signOut }), [session, loading, adoptToken, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

/** For tests: a fixed session. ``onToken`` sees tokens a page stores (credential changes). */
export function StaticAuthProvider({
  session,
  onToken,
  children,
}: {
  session: Session | null;
  onToken?: (token: string) => void;
  children: ReactNode;
}) {
  const value = useMemo(
    () => ({
      session,
      loading: false,
      adoptToken: async (token: string) => {
        setToken(token);
        onToken?.(token);
        return session as Session;
      },
      signOut: () => undefined,
    }),
    [session, onToken],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
