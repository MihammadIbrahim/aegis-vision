import { Check, Copy, RotateCw } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { Button, Dialog, Field } from "../../../components/ui";
import { creation } from "../api";
import { ROLE_LABEL } from "../labels";
import type { CodeSent, CreateStaffBody, GuardCreated, StaffRole, SupervisorCreated } from "../types";
import { formProblems, PASSWORD_HINT } from "../validation";

type Step = "password" | "admin-code" | "form" | "user-code" | "done";
const STEPS: Array<[Step, string]> = [
  ["password", "Your password"],
  ["admin-code", "Your code"],
  ["form", "Account details"],
  ["user-code", "Their code"],
];

const EMPTY_FORM = { full_name: "", username: "", email: "", phone_number: "", password: "" };

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Seconds left until a resend is allowed. */
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

function CodeStep({
  sent,
  who,
  onVerify,
  onResend,
  busy,
}: {
  sent: CodeSent;
  who: string;
  onVerify: (otp: string) => void;
  onResend: () => void;
  busy: boolean;
}) {
  const [otp, setOtp] = useState("");
  const wait = useCountdown(sent.resend_available_in_seconds, sent);
  const valid = /^[0-9]{6}$/.test(otp);
  return (
    <form
      className="stack"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        if (valid) onVerify(otp);
      }}
    >
      <p className="dialog-message">
        A 6-digit code was sent to {who} at <strong>{sent.email}</strong>. It expires in{" "}
        {Math.round(sent.otp_expires_in_seconds / 60)} minutes.
      </p>
      <Field label="6-digit code" required>
        <input
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          pattern="[0-9]{6}"
          value={otp}
          onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
        />
      </Field>
      <div className="dialog-foot">
        <Button variant="ghost" icon={RotateCw} onClick={onResend} disabled={wait > 0 || busy}>
          {wait > 0 ? `Resend in ${wait}s` : "Resend code"}
        </Button>
        <Button type="submit" loading={busy} disabled={!valid}>
          Verify
        </Button>
      </div>
    </form>
  );
}

/** Admin-only account creation, as the backend requires: the Admin's password and emailed code, then
 * the form, then the code sent to the new user's email. Only then is the account created. */
export function CreateStaffWizard({
  role,
  onClose,
  onCreated,
}: {
  role: StaffRole;
  onClose: () => void;
  onCreated: (created: GuardCreated | SupervisorCreated) => void;
}) {
  const flow = creation(role);
  const label = ROLE_LABEL[role];
  const [step, setStep] = useState<Step>("password");
  const [password, setPassword] = useState("");
  const [adminSent, setAdminSent] = useState<CodeSent | null>(null);
  const [authorizationId, setAuthorizationId] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [touched, setTouched] = useState(false);
  const [userSent, setUserSent] = useState<CodeSent | null>(null);
  const [created, setCreated] = useState<GuardCreated | SupervisorCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const problems = formProblems(form);

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

  const startOver = () => {
    setStep("password");
    setAdminSent(null);
    setUserSent(null);
    setAuthorizationId("");
    setError(null);
  };

  const field = (name: keyof typeof EMPTY_FORM) => ({
    value: form[name],
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value }),
  });

  const createdId = created ? ("guard_id" in created ? created.guard_id : created.supervisor_id) : "";
  const stepIndex = STEPS.findIndex(([s]) => s === step);

  let body: ReactNode;
  if (step === "password") {
    body = (
      <form
        className="stack"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (!password) return;
          void run(async () => {
            setAdminSent(await flow.authorize(password));
            setPassword("");
            setStep("admin-code");
          });
        }}
      >
        <p className="dialog-message">Confirm it is you: enter your own password. A code will be emailed to you.</p>
        <Field label="Your current password" required>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div className="dialog-foot">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy} disabled={!password}>
            Send me a code
          </Button>
        </div>
      </form>
    );
  } else if (step === "admin-code" && adminSent) {
    body = (
      <CodeStep
        sent={adminSent}
        who="you"
        busy={busy}
        onResend={() => void run(async () => setAdminSent(await flow.resendAuthorization(adminSent.id)))}
        onVerify={(otp) =>
          void run(async () => {
            const verified = await flow.verifyAuthorization(adminSent.id, otp);
            setAuthorizationId(verified.authorization_id);
            setStep("form");
          })
        }
      />
    );
  } else if (step === "form") {
    const show = (key: keyof typeof problems) => (touched ? problems[key] : undefined);
    body = (
      <form
        className="stack"
        noValidate
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          setTouched(true);
          if (Object.keys(problems).length) return;
          const payload: CreateStaffBody = {
            authorization_id: authorizationId,
            full_name: form.full_name.trim(),
            username: form.username.trim(),
            email: form.email.trim(),
            phone_number: form.phone_number.trim(),
            password: form.password,
          };
          void run(async () => {
            setUserSent(await flow.submit(payload));
            setStep("user-code");
          });
        }}
      >
        <div className="form-grid">
          <Field label="Full name" required error={show("full_name")}>
            <input maxLength={120} autoComplete="off" {...field("full_name")} />
          </Field>
          <Field label="Username" required hint="3–64 letters, digits, '.', '_' or '-'." error={show("username")}>
            <input maxLength={64} autoComplete="off" {...field("username")} />
          </Field>
          <Field label="Email" required hint="A code is sent here to confirm it." error={show("email")}>
            <input type="email" maxLength={254} autoComplete="off" {...field("email")} />
          </Field>
          <Field label="Phone number" required hint="7–15 digits, optionally starting with +." error={show("phone_number")}>
            <input type="tel" maxLength={40} autoComplete="off" {...field("phone_number")} />
          </Field>
          <Field label="Initial password" required hint={PASSWORD_HINT} error={show("password")} className="wide">
            <input type="password" autoComplete="new-password" maxLength={128} {...field("password")} />
          </Field>
        </div>
        <p className="hint">You set the first password and give it to the {label} in person; they can change it later.</p>
        <div className="dialog-foot">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            Send code to the {label}
          </Button>
        </div>
      </form>
    );
  } else if (step === "user-code" && userSent) {
    body = (
      <CodeStep
        sent={userSent}
        who={`the new ${label}`}
        busy={busy}
        onResend={() => void run(async () => setUserSent(await flow.resendCreation(userSent.id)))}
        onVerify={(otp) =>
          void run(async () => {
            const done = await flow.verifyCreation(userSent.id, otp);
            setCreated(done);
            setStep("done");
            onCreated(done);
          })
        }
      />
    );
  } else if (step === "done" && created) {
    body = (
      <div className="stack">
        <p className="notice success" role="status">
          {created.full_name} can now sign in as a {label}.
        </p>
        <dl className="details">
          <dt>{label} ID</dt>
          <dd>
            <span className="mono">{createdId}</span>{" "}
            <Button
              variant="ghost"
              size="sm"
              icon={copied ? Check : Copy}
              onClick={() => {
                void navigator.clipboard?.writeText(createdId);
                setCopied(true);
              }}
            >
              {copied ? "Copied" : "Copy"}
            </Button>
          </dd>
          <dt>Email</dt>
          <dd>{created.email}</dd>
          <dt>Campus</dt>
          <dd>{created.campus_name}</dd>
        </dl>
        <p className="hint">
          This permanent ID ({createdId.length} characters) is used to sign in{role === "GUARD" ? " in the Guard app" : ""}.
          {role === "GUARD" && " The new Guard is a Standby Guard until you assign a location."}
        </p>
        <div className="dialog-foot">
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    );
  }

  return (
    <Dialog open title={`Add ${label}`} onClose={onClose}>
      <div className="stack">
        {step !== "done" && (
          <ol className="wizard-steps" aria-label="Steps">
            {STEPS.map(([s, name], i) => (
              <li key={s} aria-current={s === step ? "step" : undefined} className={i < stepIndex ? "is-done" : undefined}>
                <span className="wizard-number">{i + 1}</span> {name}
              </li>
            ))}
          </ol>
        )}
        {error && (
          <div className="notice error" role="alert">
            <p>{error}</p>
            {(step === "form" || step === "user-code") && /expired|confirm your password/i.test(error) && (
              <Button variant="secondary" size="sm" onClick={startOver}>
                Start again
              </Button>
            )}
          </div>
        )}
        {body}
      </div>
    </Dialog>
  );
}
