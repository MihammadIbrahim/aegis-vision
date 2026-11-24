import { AtSign, Building2, Check, Copy, GitBranch, KeyRound, Lock, LogIn, Mail, Phone, RotateCw, UserRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../../api/client";
import { Button, buttonClass, Field, PasswordInput } from "../../components/ui";
import { emailProblem, fullNameProblem, PASSWORD_HINT, passwordProblem, phoneProblem, usernameProblem } from "../staff/validation";
import { registration } from "./api";
import { RegisterSwitch, Steps } from "./parts";
import type { RegistrationCodeSent, RegistrationCompleted } from "./types";

const EMPTY = {
  organization_name: "",
  campus_name: "",
  admin_full_name: "",
  username: "",
  email: "",
  phone_number: "",
  password: "",
  confirm: "",
};
type Form = typeof EMPTY;

/** Single spaces, no leading or trailing ones: the server refuses anything else, and the name must be
 * typed exactly like this at sign-in. */
export function cleanOrganizationName(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function organizationNameProblem(value: string): string | undefined {
  const name = cleanOrganizationName(value);
  if (!name) return "Enter the organization name.";
  if (name.length < 2 || name.length > 150) return "The organization name must be 2 to 150 characters long.";
  if (!/[\p{L}\p{N}]/u.test(name)) return "The organization name must contain letters or digits.";
  return undefined;
}

/** The first campus's name, as the server checks it (2–100 characters, letters or digits). */
export function campusNameProblem(value: string): string | undefined {
  const name = cleanOrganizationName(value);
  if (!name) return "Enter the campus name.";
  if (name.length < 2 || name.length > 100) return "The campus name must be 2 to 100 characters long.";
  if (!/[\p{L}\p{N}]/u.test(name)) return "The campus name must contain letters or digits.";
  return undefined;
}

export function registrationProblems(f: Form): Partial<Record<keyof Form, string>> {
  const found: Partial<Record<keyof Form, string>> = {
    organization_name: organizationNameProblem(f.organization_name),
    campus_name: campusNameProblem(f.campus_name),
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

/** Copies a value; the button's name says what it copies, and changes to "Copied" for a moment. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      icon={copied ? Check : Copy}
      aria-label={copied ? `${label}: copied` : label}
      onClick={() => {
        void navigator.clipboard?.writeText(value);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      }}
    >
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

/** Create an organization account: the applicant becomes its Main Admin. Two steps, as the Core API
 * requires: the form (a code is emailed), then the code. No token is issued: sign in afterwards. */
export function RegisterPage() {
  const [form, setForm] = useState<Form>(EMPTY);
  const [touched, setTouched] = useState(false);
  const [sent, setSent] = useState<RegistrationCodeSent | null>(null);
  const [otp, setOtp] = useState("");
  const [done, setDone] = useState<RegistrationCompleted | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);
  const problems = registrationProblems(form);

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
      const result = await registration.submit({
        organization_name: cleanOrganizationName(form.organization_name),
        campus_name: cleanOrganizationName(form.campus_name),
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

  const step = done ? 3 : sent ? 2 : 1;

  return (
    <div className="w-full">
      <RegisterSwitch current="organization" />
      <div className="mb-6">
        <h1 className="m-0 text-3xl font-semibold tracking-tight">Create an organization account</h1>
        <p className="mt-2 mb-0 text-fg-muted">
          For organizations: universities, companies, offices, warehouses and industrial sites. You become the organization's
          Main Admin, and its first campus is created with the name you give.
        </p>
      </div>
      <Steps step={step} last="Done" />
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {done ? (
        <section className="auth-card stack gap-4" aria-labelledby="done-title">
          <h2 id="done-title" className="m-0 flex items-center gap-3 text-xl">
            <span className="grid size-9 place-items-center rounded-full bg-success-bg text-success-text" aria-hidden="true">
              <Check className="icon" />
            </span>
            {done.organization_name} is registered
          </h2>
          <p className="m-0 text-fg-secondary">
            Keep these details. You sign in with the organization name (typed exactly), the Organization ID, your email and
            password. Admins of additional campuses need the exact organization name and this Organization ID to request
            their campus.
          </p>
          <dl className="rounded-xl border border-border bg-surface-header p-4">
            <dt>Organization name</dt>
            <dd>
              <strong>{done.organization_name}</strong>
            </dd>
            <dt>Organization ID</dt>
            <dd className="flex flex-wrap items-center gap-2">
              <span className="mono text-md font-medium">{done.organization_id}</span>
              <CopyButton value={done.organization_id} label="Copy Organization ID" />
            </dd>
            <dt>Main Admin ID</dt>
            <dd className="flex flex-wrap items-center gap-2">
              <span className="mono text-md font-medium">{done.main_admin.user_id}</span>
              <CopyButton value={done.main_admin.user_id} label="Copy Main Admin ID" />
            </dd>
            <dt>Campus</dt>
            <dd>{done.campus_name}</dd>
            <dt>Main Admin</dt>
            <dd>
              {done.main_admin.full_name} · {done.main_admin.email}
            </dd>
          </dl>
          <Link className={buttonClass("primary", "lg", "w-full")} to="/login">
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
            void run(async () => setDone(await registration.verify(sent.registration_id, otp)));
          }}
        >
          <p className="m-0 flex items-start gap-3 rounded-lg bg-info-bg p-3 text-sm text-info-text">
            <Mail className="icon mt-0.5" aria-hidden="true" />
            <span>
              A 6-digit code was sent to <strong>{sent.email}</strong>. It expires in {Math.round(sent.otp_expires_in_seconds / 60)}{" "}
              minutes. The organization is created only after this step.
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
            Verify and create the organization
          </Button>
          <Button
            variant="ghost"
            icon={RotateCw}
            className="self-center"
            disabled={wait > 0 || busy}
            onClick={() =>
              void run(async () => {
                const again = await registration.resend(sent.registration_id);
                setSent(again);
                setWait(again.resend_available_in_seconds);
              })
            }
          >
            {wait > 0 ? `Send a new code in ${wait}s` : "Send a new code"}
          </Button>
        </form>
      ) : (
        <form className="auth-form auth-card" noValidate onSubmit={submit} aria-label="Organization registration">
          <h2 className="register-section">
            <Building2 className="icon" aria-hidden="true" /> Organization
          </h2>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <Field
              label="Organization name"
              required
              icon={Building2}
              hint="Used exactly as typed when signing in."
              error={show("organization_name")}
            >
              <input maxLength={150} autoComplete="organization" {...field("organization_name")} />
            </Field>
            <Field label="Campus name" required icon={GitBranch} hint="Your first campus, e.g. Main Campus." error={show("campus_name")}>
              <input maxLength={100} {...field("campus_name")} />
            </Field>
          </div>
          <hr className="my-6 border-0 border-t border-border" />
          <h2 className="register-section">
            <UserRound className="icon" aria-hidden="true" /> Main Admin (you)
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
            Already registered? <Link to="/login">Sign in</Link>, or{" "}
            <Link to="/register/campus">add a campus to your organization</Link>.
          </p>
        </form>
      )}
    </div>
  );
}
