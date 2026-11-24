import { Eye, EyeOff, LogIn, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { platform } from "../api/platform";
import { Button, Field } from "../components/ui";
import { useAuth } from "./AuthContext";

/** Platform Super Admin sign-in: username + password only. No email code, no sign-up and no
 * self-service password reset: recovery uses the server's .env credentials (same form). */
export function LoginPage() {
  const { adoptToken } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<{ text: string; locked: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError({ text: "Enter your username and password.", locked: false });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const token = await platform.login(username.trim(), password);
      setPassword("");
      await adoptToken(token.access_token);
      navigate("/dashboard", { replace: true });
    } catch (e) {
      const locked = e instanceof ApiError && e.status === 429;
      setError({
        text: locked
          ? "Too many failed attempts. The account is locked for a while; try again later."
          : e instanceof ApiError && e.status === 401
            ? "Invalid username or password."
            : e instanceof Error
              ? e.message
              : String(e),
        locked,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <h1 className="login-title">
        <ShieldCheck className="icon" aria-hidden="true" /> AI CCTV Security
      </h1>
      <p className="muted">Platform Super Admin sign-in</p>
      {error && (
        <div className={`notice ${error.locked ? "warning" : "error"}`} role="alert">
          {error.text}
        </div>
      )}
      <form onSubmit={submit} className="card stack" noValidate aria-label="Sign in">
        <Field label="Username" required>
          <input value={username} autoComplete="username" maxLength={64} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Password" required>
          <input
            type={show ? "text" : "password"}
            value={password}
            autoComplete="current-password"
            maxLength={1024}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <label className="checkbox">
          <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} />
          {show ? <EyeOff className="icon" aria-hidden="true" /> : <Eye className="icon" aria-hidden="true" />} Show password
        </label>
        <Button type="submit" icon={LogIn} loading={busy}>
          Sign in
        </Button>
        <p className="hint">
          Forgotten credentials are restored with the emergency recovery credentials kept in the server's environment
          settings. There is no self-service reset.
        </p>
      </form>
    </div>
  );
}
