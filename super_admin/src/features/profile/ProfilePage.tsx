import { KeyRound, LifeBuoy, ShieldCheck, UserPen } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";

import { ApiError } from "../../api/client";
import { platform } from "../../api/platform";
import { useAuth } from "../../auth/AuthContext";
import { Button, Field, PageHeader, StatusBadge, useToast } from "../../components/ui";
import { formatDateTime } from "../../lib/format";

const USERNAME = /^[A-Za-z0-9._-]{3,64}$/;

/** The platform password policy (12–128 characters, a letter and a digit, not containing the
 * username, confirmed). The server checks again. */
export function passwordProblem(password: string, username: string): string | undefined {
  if (password.length < 12 || password.length > 128) return "Use 12 to 128 characters.";
  if (!/\p{L}/u.test(password) || !/\d/.test(password)) return "Use at least one letter and one digit.";
  if (username && password.toLowerCase().includes(username.toLowerCase())) return "The password must not contain the username.";
  return undefined;
}

/** Strength for the indicator: which rules are met. */
export function passwordChecks(password: string, username: string): Array<[string, boolean]> {
  return [
    ["12–128 characters", password.length >= 12 && password.length <= 128],
    ["A letter and a digit", /\p{L}/u.test(password) && /\d/.test(password)],
    ["Does not contain the username", !username || !password.toLowerCase().includes(username.toLowerCase())],
  ];
}

function Checks({ password, username }: { password: string; username: string }) {
  return (
    <ul className="plain checks" aria-label="Password requirements">
      {passwordChecks(password, username).map(([label, ok]) => (
        <li key={label} className={ok ? "ok" : "todo"}>
          {ok ? "✓" : "○"} {label}
        </li>
      ))}
    </ul>
  );
}

function message(e: unknown): string {
  if (e instanceof ApiError && e.status === 429) return "Too many incorrect passwords. The account is locked for a while.";
  return e instanceof Error ? e.message : String(e);
}

function FormCard({ id, title, icon: Icon, children }: { id: string; title: string; icon: typeof KeyRound; children: ReactNode }) {
  return (
    <section className="card" aria-labelledby={id} id={id.replace("-title", "")}>
      <h2 id={id}>
        <Icon className="icon inline-icon" aria-hidden="true" /> {title}
      </h2>
      {children}
    </section>
  );
}

/** Profile & Security Settings (page 8). Each change returns a fresh token, which replaces the stored
 * one: every older Super Admin session stops working. */
export function ProfilePage() {
  const { session, adoptToken } = useAuth();
  const toast = useToast();
  const admin = session?.admin;
  const recovery = session?.loginMethod === "RECOVERY";

  const [u, setU] = useState({ current: "", next: "" });
  const [p, setP] = useState({ current: "", next: "", confirm: "" });
  const [r, setR] = useState({ recovery_username: "", recovery_password: "", new_username: "", new_password: "", confirm: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  async function run(key: string, action: () => Promise<{ access_token: string }>, done: string, reset: () => void) {
    setBusy(key);
    setErrors((all) => ({ ...all, [key]: "" }));
    try {
      const token = await action();
      await adoptToken(token.access_token);
      reset();
      toast("success", done);
    } catch (e) {
      setErrors((all) => ({ ...all, [key]: message(e) }));
    } finally {
      setBusy(null);
    }
  }

  const usernameInvalid = u.next !== "" && !USERNAME.test(u.next);
  const newPasswordProblem = p.next ? passwordProblem(p.next, admin?.username ?? "") : undefined;
  const mismatch = p.confirm !== "" && p.confirm !== p.next;
  const recoveryProblem = r.new_password ? passwordProblem(r.new_password, r.new_username) : undefined;
  const recoveryMismatch = r.confirm !== "" && r.confirm !== r.new_password;

  return (
    <section>
      <PageHeader title="Profile & security" description="Your Super Admin account. Root credentials and recovery settings live in the server environment (.env)." />
      <div className="grid-2">
        <section className="card" aria-labelledby="profile-title">
          <h2 id="profile-title">
            <ShieldCheck className="icon inline-icon" aria-hidden="true" /> Account
          </h2>
          <dl>
            <dt>Username</dt>
            <dd>{admin?.username}</dd>
            <dt>Role</dt>
            <dd>
              <StatusBadge status="SUPER_ADMIN" label="Platform Super Admin" />
            </dd>
            <dt>Created</dt>
            <dd>{formatDateTime(admin?.created_at)}</dd>
            <dt>Last sign-in</dt>
            <dd>{formatDateTime(admin?.last_login_at)}</dd>
            <dt>This session</dt>
            <dd>{recovery ? "Emergency recovery credentials" : "Normal credentials"}</dd>
          </dl>
          <p className="hint">
            Changing the username or password signs out every other Super Admin session. A list of active sessions is not
            available.
          </p>
        </section>

        <FormCard id="username-title" title="Change username" icon={UserPen}>
          <form
            className="stack"
            noValidate
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              if (!u.current || !u.next || usernameInvalid) return;
              void run("username", () => platform.changeUsername(u.current, u.next), "Username changed.", () => setU({ current: "", next: "" }));
            }}
          >
            <Field label="New username" required hint="3–64 letters, digits, '.', '_' or '-'." error={usernameInvalid ? "Not a valid username." : undefined}>
              <input autoComplete="username" value={u.next} maxLength={64} onChange={(e) => setU({ ...u, next: e.target.value })} />
            </Field>
            <Field label="Current password" required>
              <input type="password" autoComplete="current-password" value={u.current} onChange={(e) => setU({ ...u, current: e.target.value })} />
            </Field>
            {errors.username && (
              <p className="notice error" role="alert">
                {errors.username}
              </p>
            )}
            <Button type="submit" loading={busy === "username"} disabled={!u.current || !u.next || usernameInvalid}>
              Save username
            </Button>
          </form>
        </FormCard>
      </div>

      <FormCard id="password-title" title="Change password" icon={KeyRound}>
        <form
          className="stack"
          noValidate
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (!p.current || newPasswordProblem || mismatch || !p.next) return;
            void run(
              "password",
              () => platform.changePassword(p.current, p.next, p.confirm),
              "Password changed.",
              () => setP({ current: "", next: "", confirm: "" }),
            );
          }}
        >
          <div className="form-grid">
            <Field label="Current password" required>
              <input type="password" autoComplete="current-password" value={p.current} onChange={(e) => setP({ ...p, current: e.target.value })} />
            </Field>
            <Field label="New password" required error={newPasswordProblem}>
              <input type="password" autoComplete="new-password" maxLength={128} value={p.next} onChange={(e) => setP({ ...p, next: e.target.value })} />
            </Field>
            <Field label="Confirm new password" required error={mismatch ? "The passwords do not match." : undefined}>
              <input type="password" autoComplete="new-password" maxLength={128} value={p.confirm} onChange={(e) => setP({ ...p, confirm: e.target.value })} />
            </Field>
          </div>
          <Checks password={p.next} username={admin?.username ?? ""} />
          {errors.password && (
            <p className="notice error" role="alert">
              {errors.password}
            </p>
          )}
          <Button type="submit" loading={busy === "password"} disabled={!p.current || !p.next || Boolean(newPasswordProblem) || p.confirm !== p.next}>
            Update password
          </Button>
        </form>
      </FormCard>

      <FormCard id="recovery-title" title="Emergency credential recovery" icon={LifeBuoy}>
        {!recovery ? (
          <p className="muted">
            Available only in a session opened with the emergency recovery credentials from the server's .env file. If the
            normal username or password is forgotten, sign out and sign in with those credentials, then come back here.
          </p>
        ) : (
          <form
            className="stack"
            noValidate
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              if (recoveryProblem || recoveryMismatch || Object.values(r).some((v) => !v)) return;
              void run(
                "recovery",
                () =>
                  platform.recoverCredentials({
                    recovery_username: r.recovery_username,
                    recovery_password: r.recovery_password,
                    new_username: r.new_username,
                    new_password: r.new_password,
                    confirm_new_password: r.confirm,
                  }),
                "Normal credentials restored. Use them for your next sign-in.",
                () => setR({ recovery_username: "", recovery_password: "", new_username: "", new_password: "", confirm: "" }),
              );
            }}
          >
            <p className="notice warning">
              You are in an emergency session. Re-enter the recovery credentials and choose a new normal username and password.
            </p>
            <div className="form-grid">
              <Field label="Recovery username" required>
                <input autoComplete="off" value={r.recovery_username} onChange={(e) => setR({ ...r, recovery_username: e.target.value })} />
              </Field>
              <Field label="Recovery password" required>
                <input type="password" autoComplete="off" value={r.recovery_password} onChange={(e) => setR({ ...r, recovery_password: e.target.value })} />
              </Field>
              <Field label="New username" required>
                <input autoComplete="off" maxLength={64} value={r.new_username} onChange={(e) => setR({ ...r, new_username: e.target.value })} />
              </Field>
              <Field label="New password" required error={recoveryProblem}>
                <input type="password" autoComplete="new-password" value={r.new_password} onChange={(e) => setR({ ...r, new_password: e.target.value })} />
              </Field>
              <Field label="Confirm new password" required error={recoveryMismatch ? "The passwords do not match." : undefined}>
                <input type="password" autoComplete="new-password" value={r.confirm} onChange={(e) => setR({ ...r, confirm: e.target.value })} />
              </Field>
            </div>
            {errors.recovery && (
              <p className="notice error" role="alert">
                {errors.recovery}
              </p>
            )}
            <Button
              type="submit"
              icon={LifeBuoy}
              loading={busy === "recovery"}
              disabled={Boolean(recoveryProblem) || r.confirm !== r.new_password || Object.values(r).some((v) => !v)}
            >
              Restore normal credentials
            </Button>
          </form>
        )}
      </FormCard>
    </section>
  );
}
