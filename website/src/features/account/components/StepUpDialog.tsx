import { RotateCw } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { Button, Dialog, Field, PasswordInput } from "../../../components/ui";

/** A code emailed by the Core API (CodeSentResponse). */
export interface CodeSentLike {
  id: string;
  email: string;
  otp_expires_in_seconds: number;
  resend_available_in_seconds: number;
}

function useCountdown(seconds: number, key: unknown): number {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    setLeft(seconds);
    if (seconds <= 0) return;
    const timer = window.setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [seconds, key]);
  return left;
}

/** Two steps before a sensitive action: the signed-in user's own password, then the 6-digit code emailed
 * to them (decision H). ``onConfirmed`` receives the confirmation id and performs the action; an error it
 * throws is shown in the dialog. */
export function StepUpDialog({
  title,
  intro,
  actionLabel,
  start,
  resend,
  verify,
  onConfirmed,
  onClose,
}: {
  title: string;
  intro: ReactNode;
  actionLabel: string;
  start: (password: string) => Promise<CodeSentLike>;
  resend: (id: string) => Promise<CodeSentLike>;
  verify: (id: string, otp: string) => Promise<unknown>;
  onConfirmed: (authorizationId: string) => Promise<void>;
  onClose: () => void;
}) {
  const [password, setPassword] = useState("");
  const [sent, setSent] = useState<CodeSentLike | null>(null);
  const [otp, setOtp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wait = useCountdown(sent?.resend_available_in_seconds ?? 0, sent);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function submitPassword(event: FormEvent) {
    event.preventDefault();
    if (!password) return;
    void run(async () => setSent(await start(password)));
  }

  function submitCode(event: FormEvent) {
    event.preventDefault();
    if (!sent || !/^[0-9]{6}$/.test(otp)) return;
    void run(async () => {
      await verify(sent.id, otp);
      await onConfirmed(sent.id);
    });
  }

  return (
    <Dialog open title={title} onClose={onClose}>
      {!sent ? (
        <form className="stack" onSubmit={submitPassword} noValidate>
          <div className="dialog-message">{intro}</div>
          <Field label="Your current password" required>
            <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {error && (
            <p className="notice error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-foot">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={busy} disabled={!password}>
              Send code
            </Button>
          </div>
        </form>
      ) : (
        <form className="stack" onSubmit={submitCode} noValidate>
          <p className="dialog-message">
            We emailed a 6-digit code to <strong>{sent.email}</strong>. It is valid for{" "}
            {Math.max(1, Math.round(sent.otp_expires_in_seconds / 60))} minutes.
          </p>
          <Field label="Code from the email" required>
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
            />
          </Field>
          {error && (
            <p className="notice error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-foot">
            <Button
              variant="ghost"
              icon={RotateCw}
              disabled={wait > 0 || busy}
              onClick={() => void run(async () => setSent(await resend(sent.id)))}
            >
              {wait > 0 ? `Resend in ${wait} s` : "Resend code"}
            </Button>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={busy} disabled={!/^[0-9]{6}$/.test(otp)}>
              {actionLabel}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
