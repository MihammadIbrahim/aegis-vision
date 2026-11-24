import { AtSign, Building2, Clock, Fingerprint, GitBranchPlus, KeyRound, Lock, LogIn, Mail, MailCheck, Phone, RotateCw, UserRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../../api/client";
import { Button, buttonClass, Field, PasswordInput } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { emailProblem, fullNameProblem, PASSWORD_HINT, passwordProblem, phoneProblem, usernameProblem } from "../staff/validation";
import { campusRequests } from "./api";
import { RegisterSwitch, Steps } from "./parts";
import type { CampusRequestCodeSent, CampusRequestVerified } from "./types";

const EMPTY = {
  organization_name: "",
  organization_id: "",
  campus_name: "",
  admin_full_name: "",
  username: "",
  email: "",
  phone_number: "",
  password: "",
  confirm: "",
};
type Form = typeof EMPTY;

const ORGANIZATION_ID = /^([A-Z]{6}[0-9]{11}|ORG-[0-9]{11})$/;

/** The proposed campus name: single spaces, as the server stores it. */
export function cleanCampusName(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function campusRequestProblems(f: Form): Partial<Record<keyof Form, string>> {
  const campus = cleanCampusName(f.campus_name);
  const found: Partial<Record<keyof Form, string>> = {
    // Matched exactly, like at sign-in: it is checked for presence only, never changed.
    organization_name: f.organization_name.trim() ? undefined : "Enter the organization's exact registered name.",
    organization_id: ORGANIZATION_ID.test(f.organization_id.trim().toUpperCase())
      ? undefined
      : "Enter the Organization ID: 6 capital letters followed by 11 digits.",
    campus_name: !campus
      ? "Enter the proposed campus or branch name."
      : campus.length < 2 || campus.length > 100
        ? "The campus name must be 2 to 100 characters long."
        : /[\p{L}\p{N}]/u.test(campus)
          ? undefined
          : "The campus name must contain letters or digits.",
    admin_full_name: fullNameProblem(f.admin_full_name),
    username: usernameProblem(f.username),
    email: emailProblem(f.email),
    phone_number: phoneProblem(f.phone_number),
    password: passwordProblem(f.password, f.username, f.email),
    confirm: f.confirm === f.password ? undefined : "The passwords do not match.",
  };
  return Object.fromEntries(Object.entries(found).filter(([, v]) => v)) as Partial<Record<keyof Form, string>>;
}

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 429) return e.message || "Too many attempts. Please wait a moment and try again.";
  return e instanceof Error ? e.message : String(e);
}

/** "Already Registered Organization?": apply to add a campus (branch, site) to an existing organization.
 * Details → email code → waiting for the Main Admin's approval. No token is issued: the applicant
 * signs in only after the Main Admin approves (before that, sign-in says the approval is pending). */
export function CampusRequestPage() {
  const [form, setForm] = useState<Form>(EMPTY);
  const [touched, setTouched] = useState(false);
  const [sent, setSent] = useState<CampusRequestCodeSent | null>(null);
  const [otp, setOtp] = useState("");
  const [done, setDone] = useState<CampusRequestVerified | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);
  const problems = campusRequestProblems(form);
  const step = done ? 3 : sent ? 2 : 1;

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

  function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (Object.keys(problems).length) return;
    void run(async () => {
      const result = await campusRequests.submit({
        organization_name: form.organization_name,
        organization_id: form.organization_id.trim().toUpperCase(),
        campus_name: cleanCampusName(form.campus_name),
        admin_full_name: form.admin_full_name.trim(),
        username: form.username.trim(),
        email: form.email.trim(),
        phone_number: form.phone_number.trim(),
        password: form.password,
      });
      setSent(result);
      setWait(result.resend_available_in_seconds);
      setForm((f) => ({ ...f, password: "", confirm: "" })); // never kept longer than needed
    });
  }

  const field = (key: keyof Form) => ({
    value: form[key],
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value }),
  });
  const show = (key: keyof Form) => (touched ? problems[key] : undefined);

  return (
    <div className="w-full">
      <RegisterSwitch current="campus" />
      <div className="mb-6">
        <h1 className="m-0 text-3xl font-semibold tracking-tight">Add a campus to your organization</h1>
        <p className="mt-2 mb-0 text-fg-muted">
          Already registered organization? Request a new campus, branch or site. You become its Admin once the organization's
          Main Admin approves the request.
        </p>
      </div>
      <Steps step={step} last="Approval" />
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {done ? (
        <section className="auth-card stack gap-4" aria-labelledby="pending-title">
          <h2 id="pending-title" className="m-0 flex items-center gap-3 text-xl">
            <span className="grid size-9 place-items-center rounded-full bg-success-bg text-success-text" aria-hidden="true">
              <MailCheck className="icon" />
            </span>
            Email verified: waiting for approval
          </h2>
          <p className="m-0 text-fg-secondary">
            Your request for <strong>{done.campus_name}</strong> is now with the Main Admin of{" "}
            <strong>{done.organization_name}</strong>. You can sign in once it is approved; until then, sign-in tells you the
            approval is pending.
          </p>
          <dl className="rounded-xl border border-border bg-surface-header p-4">
            <dt>Organization</dt>
            <dd>{done.organization_name}</dd>
            <dt>Proposed campus</dt>
            <dd>{done.campus_name}</dd>
            <dt>Verified email</dt>
            <dd>{done.email}</dd>
            <dt>Decision needed by</dt>
            <dd>{formatDateTime(done.decision_deadline)}</dd>
          </dl>
          <p className="m-0 flex items-start gap-2 text-sm text-fg-muted">
            <Clock className="icon mt-0.5" aria-hidden="true" />
            <span>A request that is not approved within 72 hours of verification expires, and its details are deleted.</span>
          </p>
          <Link className={buttonClass("outline", "lg", "w-full")} to="/login">
            <LogIn className="icon" aria-hidden="true" /> Go to sign-in
          </Link>
        </section>
      ) : sent ? (
        <form
          className="auth-form auth-card stack gap-4"
          aria-label="Verify email"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (!/^[0-9]{6}$/.test(otp)) return;
            void run(async () => setDone(await campusRequests.verify(sent.application_id, otp)));
          }}
        >
          <p className="m-0 flex items-start gap-3 rounded-lg bg-info-bg p-3 text-sm text-info-text">
            <Mail className="icon mt-0.5" aria-hidden="true" />
            <span>
              A 6-digit code was sent to <strong>{sent.email}</strong>. It expires in {Math.round(sent.otp_expires_in_seconds / 60)}{" "}
              minutes. The request reaches the Main Admin only after this step.
            </span>
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
            Verify and send the request
          </Button>
          <Button
            variant="ghost"
            icon={RotateCw}
            className="self-center"
            disabled={wait > 0 || busy}
            onClick={() =>
              void run(async () => {
                const again = await campusRequests.resend(sent.application_id);
                setSent(again);
                setWait(again.resend_available_in_seconds);
              })
            }
          >
            {wait > 0 ? `Send a new code in ${wait}s` : "Send a new code"}
          </Button>
        </form>
      ) : (
        <form className="auth-form auth-card" noValidate onSubmit={submit} aria-label="Campus request">
          <h2 className="register-section">
            <Building2 className="icon" aria-hidden="true" /> Your organization
          </h2>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <Field
              label="Organization name"
              required
              icon={Building2}
              className="sm:col-span-2"
              hint="Exactly as registered, including capitals and spaces."
              error={show("organization_name")}
            >
              <input maxLength={150} autoComplete="organization" {...field("organization_name")} />
            </Field>
            <Field label="Organization ID" required icon={Fingerprint} hint="Ask your Main Admin." error={show("organization_id")}>
              <input maxLength={17} spellCheck={false} className="font-mono uppercase" {...field("organization_id")} />
            </Field>
            <Field label="Proposed campus name" required icon={GitBranchPlus} hint="e.g. North Campus" error={show("campus_name")}>
              <input maxLength={100} {...field("campus_name")} />
            </Field>
          </div>
          <hr className="my-6 border-0 border-t border-border" />
          <h2 className="register-section">
            <UserRound className="icon" aria-hidden="true" /> Campus Admin (you)
          </h2>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <Field label="Full name" required icon={UserRound} error={show("admin_full_name")}>
              <input maxLength={120} autoComplete="name" {...field("admin_full_name")} />
            </Field>
            <Field label="Username" required icon={AtSign} hint="3–64 letters, digits, '.', '_' or '-'." error={show("username")}>
              <input maxLength={64} autoComplete="username" {...field("username")} />
            </Field>
            <Field label="Official email" required icon={Mail} hint="A verification code is sent here." error={show("email")}>
              <input type="email" maxLength={254} autoComplete="email" {...field("email")} />
            </Field>
            <Field label="Phone number" required icon={Phone} hint="7–15 digits, optionally starting with +." error={show("phone_number")}>
              <input type="tel" maxLength={40} autoComplete="tel" {...field("phone_number")} />
            </Field>
            <Field label="Password" required hint={PASSWORD_HINT} error={show("password")}>
              <PasswordInput icon={Lock} maxLength={128} autoComplete="new-password" {...field("password")} />
            </Field>
            <Field label="Confirm password" required error={show("confirm")}>
              <PasswordInput icon={Lock} revealLabel="confirmation" maxLength={128} autoComplete="new-password" {...field("confirm")} />
            </Field>
          </div>
          <Button type="submit" size="lg" className="mt-6 w-full" loading={busy}>
            Send verification code
          </Button>
          <p className="mt-4 mb-0 border-t border-border pt-4 text-center text-sm text-fg-muted">
            New organization? <Link to="/register">Create an organization account</Link>. Already approved?{" "}
            <Link to="/login">Sign in</Link>.
          </p>
        </form>
      )}
    </div>
  );
}
