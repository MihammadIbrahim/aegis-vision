import { KeyRound, Pencil, RefreshCw } from "lucide-react";
import { useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { Button, ErrorState, Field, FormDialog, LoadingState, PageHeader, PasswordInput, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { adminProfile, supervisorProfile, type ProfileChangeBody, type ProfileChangeStarted } from "../api";

interface Shown {
  id: string;
  idLabel: string;
  full_name: string;
  username: string | null;
  email: string;
  phone_number: string | null;
  organization_name: string;
  organization_id: string;
  campus_name?: string;
  is_main_admin?: boolean;
}

type Flow = typeof adminProfile | typeof supervisorProfile;

/** Edit: the current password, then the code emailed to the current address (and one to a new email). */
function EditProfile({ profile, flow, allowPhone, onClose, onDone }: { profile: Shown; flow: Flow; allowPhone: boolean; onClose: () => void; onDone: (signedOut: boolean) => void }) {
  const [form, setForm] = useState({
    full_name: profile.full_name,
    username: profile.username ?? "",
    email: profile.email,
    phone_number: profile.phone_number ?? "",
    new_password: "",
    confirm_new_password: "",
    current_password: "",
  });
  const [started, setStarted] = useState<ProfileChangeStarted | null>(null);
  const [otp, setOtp] = useState("");
  const [newOtp, setNewOtp] = useState("");
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  if (started) {
    return (
      <FormDialog
        title="Confirm the changes"
        submitLabel="Confirm and save"
        disabled={!/^[0-9]{6}$/.test(otp) || (started.new_email !== null && !/^[0-9]{6}$/.test(newOtp))}
        onClose={() => {
          void flow.cancel(started.change_id).catch(() => undefined);
          onClose();
        }}
        onSubmit={async () => {
          const result = await flow.verify(started.change_id, otp, started.new_email ? newOtp : null);
          onDone(result.signed_out);
        }}
      >
        <p className="dialog-message">
          Nothing changes until you enter the code sent to <strong>{started.email}</strong>
          {started.new_email && (
            <>
              {" "}
              and the code sent to the new address <strong>{started.new_email}</strong>
            </>
          )}
          .
        </p>
        <Field label="Code sent to your current email" required>
          <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} />
        </Field>
        {started.new_email && (
          <Field label="Code sent to the new email" required>
            <input inputMode="numeric" maxLength={6} value={newOtp} onChange={(e) => setNewOtp(e.target.value.replace(/\D/g, ""))} />
          </Field>
        )}
        <Button variant="ghost" icon={RefreshCw} onClick={() => void flow.resend(started.change_id).then(setStarted)}>
          Resend codes
        </Button>
      </FormDialog>
    );
  }
  return (
    <FormDialog
      title="Edit your profile"
      submitLabel="Send code"
      disabled={!form.current_password}
      onClose={onClose}
      onSubmit={async () => {
        const body: ProfileChangeBody = { current_password: form.current_password };
        if (form.full_name.trim() !== profile.full_name) body.full_name = form.full_name.trim();
        if (form.username.trim() && form.username.trim() !== profile.username) body.username = form.username.trim();
        if (form.email.trim().toLowerCase() !== profile.email.toLowerCase()) body.email = form.email.trim();
        if (allowPhone && form.phone_number.trim() && form.phone_number.trim() !== profile.phone_number) body.phone_number = form.phone_number.trim();
        if (form.new_password) {
          body.new_password = form.new_password;
          body.confirm_new_password = form.confirm_new_password;
        }
        setStarted(await flow.start(body));
      }}
    >
      <div className="form-grid">
        <Field label="Full name" required>
          <input value={form.full_name} maxLength={120} onChange={set("full_name")} />
        </Field>
        <Field label="Username" required>
          <input value={form.username} maxLength={64} onChange={set("username")} autoComplete="username" />
        </Field>
        <Field label="Official email" hint="A new email is verified with its own code.">
          <input type="email" value={form.email} maxLength={254} onChange={set("email")} />
        </Field>
        {allowPhone && (
          <Field label="Phone number">
            <input type="tel" value={form.phone_number} maxLength={40} onChange={set("phone_number")} />
          </Field>
        )}
        <Field label="New password" hint="Leave empty to keep your password. Every session signs out when it changes.">
          <PasswordInput value={form.new_password} autoComplete="new-password" onChange={set("new_password")} />
        </Field>
        <Field label="Confirm the new password">
          <PasswordInput value={form.confirm_new_password} autoComplete="new-password" onChange={set("confirm_new_password")} revealLabel="confirm password" />
        </Field>
      </div>
      <Field label="Your current password" required>
        <PasswordInput value={form.current_password} autoComplete="current-password" onChange={set("current_password")} />
      </Field>
    </FormDialog>
  );
}

/** The signed-in user's own profile: Admins (page 24) and Supervisors (page 19). */
export function ProfilePage() {
  const { session, signOut } = useAuth();
  const toast = useToast();
  const isAdmin = session?.role === "ADMIN";
  const loaded = useAsync<Shown>(async () => {
    if (isAdmin) {
      const p = await adminProfile.get();
      return { ...p, id: p.admin_id, idLabel: "Admin ID" };
    }
    const p = await supervisorProfile.get();
    return { ...p, id: p.supervisor_id, idLabel: "Supervisor ID" };
  }, [isAdmin]);
  const [editing, setEditing] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [password, setPassword] = useState("");

  if (loaded.error) return <ErrorState error={loaded.error} onRetry={loaded.reload} />;
  if (!loaded.data) return <LoadingState />;
  const p = loaded.data;
  return (
    <section>
      <PageHeader
        title="Profile"
        description="Your own account. Changes need your current password and a code sent to your email."
        actions={
          <>
            <Button variant="secondary" icon={Pencil} onClick={() => setEditing(true)}>
              Edit profile
            </Button>
            {isAdmin && (
              <Button variant="secondary" icon={KeyRound} onClick={() => setRegenerating(true)}>
                New Admin ID
              </Button>
            )}
          </>
        }
      />
      <div className="grid-2">
        <section className="card" aria-labelledby="profile-account">
          <h2 id="profile-account">Account</h2>
          <dl className="detail-list">
            <div><dt>{p.idLabel}</dt><dd className="mono">{p.id}</dd></div>
            <div><dt>Name</dt><dd>{p.full_name}</dd></div>
            <div><dt>Username</dt><dd>{p.username ?? "—"}</dd></div>
            <div><dt>Official email</dt><dd className="break-anywhere">{p.email}</dd></div>
            <div><dt>Phone</dt><dd>{p.phone_number ?? "—"}</dd></div>
            <div><dt>Role</dt><dd>{isAdmin ? (p.is_main_admin ? "Main Admin" : "Organization Admin") : "Supervisor"}</dd></div>
          </dl>
          {!isAdmin && <p className="muted small">Supervisor IDs are permanent. Your phone number is changed by an Admin.</p>}
        </section>
        <section className="card" aria-labelledby="profile-org">
          <h2 id="profile-org">Organization</h2>
          <dl className="detail-list">
            <div><dt>Organization</dt><dd>{p.organization_name}</dd></div>
            <div><dt>Organization ID</dt><dd className="mono">{p.organization_id}</dd></div>
            {p.campus_name && <div><dt>Campus</dt><dd>{p.campus_name}</dd></div>}
          </dl>
        </section>
      </div>
      {editing && (
        <EditProfile
          profile={p}
          flow={isAdmin ? adminProfile : supervisorProfile}
          allowPhone={isAdmin}
          onClose={() => setEditing(false)}
          onDone={(signedOut) => {
            setEditing(false);
            if (signedOut) {
              toast("success", "Saved. Your password changed, so please sign in again.");
              signOut();
            } else {
              toast("success", "Your profile was saved.");
              loaded.reload();
            }
          }}
        />
      )}
      {regenerating && (
        <FormDialog
          title="Get a new Admin ID"
          submitLabel="Get new ID"
          disabled={!password}
          onClose={() => {
            setRegenerating(false);
            setPassword("");
          }}
          onSubmit={async () => {
            const result = await adminProfile.regenerateId(password);
            toast("success", `Your new Admin ID is ${result.display_id}. Your history stays linked.`);
            setRegenerating(false);
            setPassword("");
            loaded.reload();
          }}
        >
          <p className="dialog-message">
            Your displayed Admin ID changes; your account, sessions and history stay the same. Use the new ID from now on.
          </p>
          <Field label="Your current password" required>
            <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </FormDialog>
      )}
    </section>
  );
}
