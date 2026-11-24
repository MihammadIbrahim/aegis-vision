import { Check, Fingerprint, IdCard, KeyRound, Lock, LogIn, Mail, RotateCw } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { Button, buttonClass, Field, PasswordInput } from "../components/ui";
import { emailProblem, PASSWORD_HINT, passwordProblem } from "../features/staff/validation";
import { passwordReset, type PasswordResetStarted } from "./api";

type IdKind = "organization" | "supervisor";

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 429) return e.message || "Too many attempts. Please wait a moment and try again.";
  return e instanceof Error ? e.message : String(e);
}

/** Forgot password for Organization Admins and Supervisors (POST /auth/password-reset…):
 * 1. Organization ID (Admins and Supervisors) or Supervisor ID, plus the registered email. The server
 *    answers the same whether or not an account matches, and emails a code only when one does.
 * 2. The 6-digit code → a one-time reset token (kept in memory only).
 * 3. The new password twice. Every earlier session is signed out; then sign in again. */
export function ForgotPasswordPage() {
  const [kind, setKind] = useState<IdKind>("organization");
  const [identifier, setIdentifier] = useState("");
  const [email, setEmail] = useState("");
  const [touched, setTouched] = useState(false);
  const [started, setStarted] = useState<PasswordResetStarted | null>(null);
  const [otp, setOtp] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);

  useEffect(() => {
    if (wait <= 0) return;
    const timer = window.setTimeout(() => setWait((w) => w - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [wait]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const id = identifier.trim().toUpperCase();
  const idProblem = id ? undefined : kind === "organization" ? "Enter the Organization ID." : "Enter your Supervisor ID.";
  const mailProblem = emailProblem(email);
  const newPasswordProblem = passwordProblem(password, "", email.trim());
  const confirmProblem = confirm === password ? undefined : "The passwords do not match.";

  return (
    <div className="w-full">
      <div className="mb-6">
        <p className="m-0 mb-2 font-mono text-xs uppercase tracking-[0.14em] text-fg-muted">Account recovery</p>
        <h1 className="m-0 text-3xl font-semibold tracking-tight">Reset your password</h1>
        <p className="mt-2 mb-0 text-fg-muted">For Organization Admins and Supervisors. Guards reset their password in the mobile app.</p>
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {done ? (
        <section className="auth-card stack gap-4" aria-labelledby="reset-done">
          <h2 id="reset-done" className="m-0 flex items-center gap-3 text-xl">
            <span className="grid size-9 place-items-center rounded-full bg-success-bg text-success-text" aria-hidden="true">
              <Check className="icon" />
            </span>
            Password changed
          </h2>
          <p className="m-0 text-fg-secondary">{done}</p>
          <Link className={buttonClass("primary", "lg", "w-full")} to="/login">
            <LogIn className="icon" aria-hidden="true" /> Go to sign-in
          </Link>
        </section>
      ) : token ? (
        <form
          className="auth-form auth-card stack gap-4"
          aria-label="Choose a new password"
          noValidate
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setTouched(true);
            if (newPasswordProblem || confirmProblem || !started) return;
            void run(async () => {
              const changed = await passwordReset.complete(started.reset_id, token, password, confirm);
              setPassword("");
              setConfirm("");
              setToken(null);
              setDone(changed.message);
            });
          }}
        >
          <Field label="New password" required hint={PASSWORD_HINT} error={touched ? newPasswordProblem : undefined}>
            <PasswordInput icon={Lock} maxLength={128} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Field label="Confirm new password" required error={touched ? confirmProblem : undefined}>
            <PasswordInput
              icon={Lock}
              revealLabel="confirmation"
              maxLength={128}
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </Field>
          <Button type="submit" size="lg" className="w-full" loading={busy}>
            Change password
          </Button>
        </form>
      ) : started ? (
        <form
          className="auth-form auth-card stack gap-4"
          aria-label="Verify email"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (!/^[0-9]{6}$/.test(otp)) return;
            void run(async () => {
              const result = await passwordReset.verify(started.reset_id, otp);
              setToken(result.reset_token);
              setTouched(false);
            });
          }}
        >
          <p className="m-0 flex items-start gap-3 rounded-lg bg-info-bg p-3 text-sm text-info-text">
            <Mail className="icon mt-0.5" aria-hidden="true" />
            <span>{started.message}</span>
          </p>
          <Field label="Verification code" required icon={KeyRound}>
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              className="font-mono text-lg tracking-[0.4em]"
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
            />
          </Field>
          <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!/^[0-9]{6}$/.test(otp)}>
            Verify code
          </Button>
          <Button
            variant="ghost"
            icon={RotateCw}
            className="self-center"
            disabled={wait > 0 || busy}
            onClick={() =>
              void run(async () => {
                const again = await passwordReset.resend(started.reset_id);
                setStarted(again);
                setWait(again.resend_available_in_seconds);
              })
            }
          >
            {wait > 0 ? `Send a new code in ${wait}s` : "Send a new code"}
          </Button>
        </form>
      ) : (
        <form
          className="auth-form auth-card stack gap-4"
          aria-label="Find your account"
          noValidate
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setTouched(true);
            if (idProblem || mailProblem) return;
            void run(async () => {
              const body = kind === "organization" ? { organization_id: id, email: email.trim() } : { supervisor_id: id, email: email.trim() };
              const result = await passwordReset.start(body);
              setStarted(result);
              setWait(result.resend_available_in_seconds);
              setTouched(false);
            });
          }}
        >
          <fieldset className="m-0 flex gap-1 rounded-xl border border-border bg-surface-header p-1">
            <legend className="sr-only">Find the account by</legend>
            {(
              [
                ["organization", "Use Organization ID"],
                ["supervisor", "Use Supervisor ID"],
              ] as const
            ).map(([value, label]) => (
              <label
                key={value}
                className={[
                  "flex flex-1 cursor-pointer flex-row items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium",
                  kind === value ? "bg-surface text-fg shadow-card" : "text-fg-muted",
                ].join(" ")}
              >
                <input type="radio" name="id-kind" className="sr-only" checked={kind === value} onChange={() => setKind(value)} />
                {label}
              </label>
            ))}
          </fieldset>
          <Field
            label={kind === "organization" ? "Organization ID" : "Supervisor ID"}
            required
            icon={kind === "organization" ? Fingerprint : IdCard}
            hint={kind === "organization" ? "Admins and Supervisors: your organization's ID." : "Supervisors only: your own Supervisor ID."}
            error={touched ? idProblem : undefined}
          >
            <input spellCheck={false} className="font-mono uppercase" maxLength={40} value={identifier} onChange={(e) => setIdentifier(e.target.value)} />
          </Field>
          <Field label="Email" required icon={Mail} hint="The email address of your account." error={touched ? mailProblem : undefined}>
            <input type="email" autoComplete="email" maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Button type="submit" size="lg" className="w-full" loading={busy}>
            Send verification code
          </Button>
          <p className="m-0 border-t border-border pt-4 text-center text-sm text-fg-muted">
            Remembered it? <Link to="/login">Sign in</Link>
          </p>
        </form>
      )}
    </div>
  );
}
