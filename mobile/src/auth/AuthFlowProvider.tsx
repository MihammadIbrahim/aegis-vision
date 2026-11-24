import { createContext, type ReactNode, useContext, useMemo, useState } from "react";

import type { LoginCodeSentResponse, OperationalMode, PasswordResetStartedResponse } from "../api/types";

/** In-memory hand-over between the sign-in and code screens (never written to disk). */
export interface PendingLogin {
  sent: LoginCodeSentResponse;
  mode: OperationalMode;
}

export interface PendingReset {
  started: PasswordResetStartedResponse;
  maskedEmail: string;
}

interface AuthFlowValue {
  pendingLogin: PendingLogin | null;
  setPendingLogin: (value: PendingLogin | null) => void;
  pendingReset: PendingReset | null;
  setPendingReset: (value: PendingReset | null) => void;
}

const AuthFlowContext = createContext<AuthFlowValue | null>(null);

export function AuthFlowProvider({ children }: { children: ReactNode }) {
  const [pendingLogin, setPendingLogin] = useState<PendingLogin | null>(null);
  const [pendingReset, setPendingReset] = useState<PendingReset | null>(null);
  const value = useMemo(
    () => ({ pendingLogin, setPendingLogin, pendingReset, setPendingReset }),
    [pendingLogin, pendingReset],
  );
  return <AuthFlowContext.Provider value={value}>{children}</AuthFlowContext.Provider>;
}

export function useAuthFlow(): AuthFlowValue {
  const value = useContext(AuthFlowContext);
  if (!value) throw new Error("useAuthFlow must be used inside AuthFlowProvider");
  return value;
}

/** Masks an email like the server does (gu***@company.com), for the reset screens. */
export function maskEmail(email: string): string {
  const [local, domain] = email.trim().split("@");
  if (!domain) return email;
  return `${local.slice(0, 2)}***@${domain}`;
}
