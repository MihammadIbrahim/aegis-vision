import { Building2, Fingerprint, KeyRound, Lock, Mail } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { auth, type LoginForm } from "./api";
import { useAuth } from "./AuthContext";
import { Button, ErrorState, Field, PasswordInput } from "../components/ui";

// Organization Admins and Supervisors sign in with the organization's name and ID, their email and
// password, then the 6-digit code sent to their email.
export function LoginPage() {
  const { signIn, signOut, notice } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState<LoginForm>({ organization_name: "", organization_id: "", email: "", password: "" });
  const [loginId, setLoginId] = useState<string | null>(null);
  const [maskedEmail, setMaskedEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function start(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const sent = await auth.start(form);
      setLoginId(sent.login_id);
      setMaskedEmail(sent.email);
      setForm((f) => ({ ...f, password: "" })); // never kept longer than needed
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    if (!loginId) return;
    setBusy(true);
    setError(null);
    try {
      const token = await auth.verify(loginId, otp);
      const session = await signIn(token.access_token);
      if (session.role === "GUARD") {
        signOut();
        setError(new Error("Guards use the mobile app. This web app is for Admins and Supervisors."));
        setLoginId(null);
        return;
      }
      navigate("/dashboard"); // Admins and Supervisors both start on their dashboard
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const set = (key: keyof LoginForm) => ({
    value: form[key],
    required: true,
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value }),
  });

  return (
    <div className="w-full">
      <div className="mb-6">
        <p className="m-0 mb-2 font-mono text-xs uppercase tracking-[0.14em] text-fg-muted">AI CCTV Security</p>
        <h1 className="m-0 text-3xl font-semibold tracking-tight">{loginId === null ? "Sign in" : "Check your email"}</h1>
        <p className="mt-2 mb-0 text-fg-muted">Organization Admin and Supervisor sign-in</p>
      </div>
      {notice && loginId === null ? (
        <p className="notice warning" role="alert" data-testid="signout-notice">
          {notice.message}
        </p>
      ) : null}
      {error ? <ErrorState error={error} /> : null}
      {loginId === null ? (
        <form onSubmit={start} className="auth-form auth-card stack gap-4">
          <Field label="Organization name" required icon={Building2}>
            <input autoComplete="organization" placeholder="As registered" {...set("organization_name")} />
          </Field>
          <Field label="Organization ID" required icon={Fingerprint}>
            <input autoComplete="off" spellCheck={false} className="font-mono" placeholder="e.g. ABCDEF12345678901" {...set("organization_id")} />
          </Field>
          <Field label="Email" required icon={Mail}>
            <input type="email" autoComplete="email" placeholder="you@organization.com" {...set("email")} />
          </Field>
          <Field label="Password" required>
            <PasswordInput icon={Lock} autoComplete="current-password" {...set("password")} />
          </Field>
          <Link className="-mt-2 self-end text-sm" to="/forgot-password">
            Forgot password?
          </Link>
          <Button type="submit" size="lg" className="w-full mt-2" disabled={busy} loading={busy}>
            Continue
          </Button>
          <div className="flex flex-col gap-1.5 border-t border-border pt-4 text-center text-sm text-fg-muted">
            <p className="m-0">
              New organization? <Link to="/register">Create an account</Link>
            </p>
            <p className="m-0">
              Already registered organization? <Link to="/register/campus">Add a campus / branch</Link>
            </p>
          </div>
        </form>
      ) : (
        <form onSubmit={verify} className="auth-form auth-card stack gap-4">
          <p className="m-0 flex items-start gap-3 rounded-lg bg-info-bg p-3 text-sm text-info-text">
            <Mail className="icon mt-0.5" aria-hidden="true" />
            <span>Enter the 6-digit code sent to {maskedEmail}.</span>
          </p>
          <Field label="Verification code" required icon={KeyRound}>
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              className="font-mono text-lg tracking-[0.4em]"
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
            />
          </Field>
          <Button type="submit" size="lg" className="w-full" disabled={busy} loading={busy}>
            Sign in
          </Button>
          <button type="button" className="link self-center" onClick={() => auth.resend(loginId).catch(setError)}>
            Send a new code
          </button>
        </form>
      )}
    </div>
  );
}
