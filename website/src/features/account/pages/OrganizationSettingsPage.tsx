import { Save } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Button, ErrorState, Field, FormDialog, LoadingState, PageHeader, PasswordInput, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { organization, type OrganizationSettings } from "../api";

const MODULE_LABELS: Record<string, string> = {
  human_detection: "Human detection & tracking",
  person_reid: "Person re-identification",
  weapon_detection: "Weapon detection",
  fight_detection: "Fight / violence detection",
  face_recognition: "Restricted-area face recognition",
};

function zones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  return intl.supportedValuesOf ? intl.supportedValuesOf("timeZone") : ["Asia/Karachi", "UTC"];
}

function SettingsForm({ settings, onSaved, onCancel }: { settings: OrganizationSettings; onSaved: (s: OrganizationSettings) => void; onCancel: () => void }) {
  const [form, setForm] = useState({
    official_phone: settings.profile.official_phone ?? "",
    address_line: settings.profile.address_line ?? "",
    city: settings.profile.city ?? "",
    country: settings.profile.country ?? "",
    time_zone: settings.preferences.time_zone,
    date_format: settings.preferences.date_format,
    time_format: settings.preferences.time_format,
    auto_escalation_enabled: settings.alert_defaults.auto_escalation_enabled,
    escalation_high_seconds: settings.alert_defaults.escalation_high_seconds,
    escalation_standard_seconds: settings.alert_defaults.escalation_standard_seconds,
    assistance_broadcast_enabled: settings.alert_defaults.assistance_broadcast_enabled,
    current_password: "",
  });
  const text = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  return (
    <FormDialog
      title="Edit organization settings"
      submitLabel="Save settings"
      disabled={!form.current_password}
      onClose={onCancel}
      onSubmit={async () => {
        onSaved(
          await organization.save({
            ...form,
            official_phone: form.official_phone.trim() || null,
            address_line: form.address_line.trim() || null,
            city: form.city.trim() || null,
            country: form.country.trim() || null,
            escalation_high_seconds: Number(form.escalation_high_seconds),
            escalation_standard_seconds: Number(form.escalation_standard_seconds),
          }),
        );
      }}
    >
      <h3 className="dash-subhead">Contact and address</h3>
      <div className="form-grid">
        <Field label="Official phone">
          <input type="tel" value={form.official_phone} maxLength={40} onChange={text("official_phone")} />
        </Field>
        <Field label="Address">
          <input value={form.address_line} maxLength={300} onChange={text("address_line")} />
        </Field>
        <Field label="City">
          <input value={form.city} maxLength={100} onChange={text("city")} />
        </Field>
        <Field label="Country">
          <input value={form.country} maxLength={100} onChange={text("country")} />
        </Field>
      </div>
      <h3 className="dash-subhead">Display preferences</h3>
      <div className="form-grid">
        <Field label="Time zone" required>
          <select value={form.time_zone} onChange={text("time_zone")}>
            {zones().map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Date format" required>
          <select value={form.date_format} onChange={text("date_format")}>
            <option value="DD/MM/YYYY">DD/MM/YYYY</option>
            <option value="MM/DD/YYYY">MM/DD/YYYY</option>
            <option value="YYYY-MM-DD">YYYY-MM-DD</option>
          </select>
        </Field>
        <Field label="Time format" required>
          <select value={form.time_format} onChange={text("time_format")}>
            <option value="24H">24-hour</option>
            <option value="12H">12-hour</option>
          </select>
        </Field>
      </div>
      <h3 className="dash-subhead">Alert defaults</h3>
      <label className="checkbox small">
        <input type="checkbox" checked={form.auto_escalation_enabled} onChange={(e) => setForm({ ...form, auto_escalation_enabled: e.target.checked })} />
        Escalate unacknowledged alerts to every Guard of the campus automatically
      </label>
      <div className="form-grid">
        <Field label="High priority: after (seconds)" hint="30 to 3600">
          <input type="number" min={30} max={3600} value={form.escalation_high_seconds} onChange={(e) => setForm({ ...form, escalation_high_seconds: Number(e.target.value) })} />
        </Field>
        <Field label="Standard: after (seconds)" hint="30 to 86400">
          <input type="number" min={30} max={86400} value={form.escalation_standard_seconds} onChange={(e) => setForm({ ...form, escalation_standard_seconds: Number(e.target.value) })} />
        </Field>
      </div>
      <label className="checkbox small">
        <input type="checkbox" checked={form.assistance_broadcast_enabled} onChange={(e) => setForm({ ...form, assistance_broadcast_enabled: e.target.checked })} />
        When a Guard asks for additional Guards, also alert Standby Guards and Guards of the same building
      </label>
      <Field label="Your current password" required hint="Organization settings are saved only with your password.">
        <PasswordInput value={form.current_password} autoComplete="current-password" onChange={text("current_password")} />
      </Field>
    </FormDialog>
  );
}

/** Organization Settings (Org Admin page 25). Every Admin can read them; only the Main Admin changes them. */
export function OrganizationSettingsPage() {
  const toast = useToast();
  const loaded = useAsync(() => organization.settings(), []);
  const [editing, setEditing] = useState(false);
  if (loaded.error) return <ErrorState error={loaded.error} onRetry={loaded.reload} />;
  if (!loaded.data) return <LoadingState />;
  const s = loaded.data;
  const minutes = (seconds: number) => (seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`);
  return (
    <section>
      <PageHeader
        title="Organization Settings"
        description={s.can_edit ? "You are the Main Admin: you change these settings for the whole organization." : "Only the Main Admin changes organization-wide settings; you can read them."}
        actions={
          s.can_edit ? (
            <Button icon={Save} onClick={() => setEditing(true)}>
              Edit settings
            </Button>
          ) : undefined
        }
      />
      <div className="grid-2">
        <section className="card" aria-labelledby="org-profile">
          <h2 id="org-profile">Organization profile</h2>
          <dl className="detail-list">
            <div><dt>Name</dt><dd>{s.profile.name}</dd></div>
            <div><dt>Organization ID</dt><dd className="mono">{s.profile.organization_code}</dd></div>
            <div><dt>Official email</dt><dd className="break-anywhere">{s.profile.official_email ?? "—"} <span className="muted small">(fixed: the verified registration email)</span></dd></div>
            <div><dt>Official phone</dt><dd>{s.profile.official_phone ?? "—"}</dd></div>
            <div><dt>Address</dt><dd>{[s.profile.address_line, s.profile.city, s.profile.country].filter(Boolean).join(", ") || "—"}</dd></div>
            <div><dt>Registered</dt><dd>{formatDateTime(s.profile.registered_at)}</dd></div>
          </dl>
        </section>
        <section className="card" aria-labelledby="org-scope">
          <h2 id="org-scope">Your scope</h2>
          <dl className="detail-list">
            <div><dt>Role</dt><dd>{s.is_main_admin ? "Main Admin (approves campuses)" : "Campus Admin (manages this campus)"}</dd></div>
            <div><dt>Campus</dt><dd>{s.campus.campus_name}</dd></div>
            {s.campus.pending_campus_requests !== null && (
              <div><dt>Campus requests</dt><dd><Link to="/campus-requests">{s.campus.pending_campus_requests} waiting</Link></dd></div>
            )}
          </dl>
        </section>
        <section className="card" aria-labelledby="org-prefs">
          <h2 id="org-prefs">Operational preferences</h2>
          <dl className="detail-list">
            <div><dt>Time zone</dt><dd>{s.preferences.time_zone}</dd></div>
            <div><dt>Date / time format</dt><dd>{s.preferences.date_format} · {s.preferences.time_format === "24H" ? "24-hour" : "12-hour"}</dd></div>
            <div>
              <dt>Automatic escalation</dt>
              <dd>
                {s.alert_defaults.auto_escalation_enabled
                  ? `On: high priority after ${minutes(s.alert_defaults.escalation_high_seconds)}, standard after ${minutes(s.alert_defaults.escalation_standard_seconds)}`
                  : "Off"}
              </dd>
            </div>
            <div><dt>Assistance broadcast</dt><dd>{s.alert_defaults.assistance_broadcast_enabled ? "On" : "Off"}</dd></div>
          </dl>
          {s.updated_at && <p className="muted small">Last changed {formatDateTime(s.updated_at)} by {s.updated_by_name ?? "—"}.</p>}
        </section>
        <section className="card" aria-labelledby="org-security">
          <h2 id="org-security">Sign-in security rules</h2>
          <ul className="plain stack small">
            <li>An emailed code is required at every sign-in; it expires after {Math.round(s.security.code_expires_seconds / 60)} minutes ({s.security.code_max_attempts} attempts).</li>
            <li>After {s.security.lockout_after_failed_sign_ins} wrong passwords the account is locked for {s.security.lockout_minutes} minutes.</li>
            <li>Passwords: {s.security.password_min_length}–{s.security.password_max_length} characters with a letter and a digit, not the username or email.</li>
          </ul>
        </section>
        <section className="card" aria-labelledby="org-policy">
          <h2 id="org-policy">Storage &amp; AI summary (this campus)</h2>
          <dl className="detail-list">
            <div><dt>Recording retention</dt><dd>{s.campus.retention_label}{s.campus.retention_is_default ? " (server default)" : ""} · <Link to="/recordings">change</Link></dd></div>
            <div><dt>AI modules permitted</dt><dd>{s.campus.ai_modules.length ? s.campus.ai_modules.map((m) => MODULE_LABELS[m] ?? m).join(", ") : "None"} · <Link to="/ai-configuration">configure</Link></dd></div>
          </dl>
        </section>
        <section className="card" aria-labelledby="org-requests">
          <h2 id="org-requests">“Already Registered Organization?” requests</h2>
          <ul className="plain stack small">
            <li>Applicants give the exact organization name and Organization ID, and verify their email with a code.</li>
            <li>The Main Admin decides within {s.campus_requests.decision_window_hours} hours of that verification; later requests expire.</li>
            <li>Approval needs the Main Admin's password and an emailed code; rejection needs the password and erases the applicant's details.</li>
          </ul>
        </section>
      </div>
      {editing && (
        <SettingsForm
          settings={s}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            toast("success", "Organization settings saved.");
            loaded.reload();
          }}
        />
      )}
    </section>
  );
}
