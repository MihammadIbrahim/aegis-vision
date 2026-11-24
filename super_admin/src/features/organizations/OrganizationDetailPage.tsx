import { Ban, CircleCheck, Pencil, Power, ShieldOff } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { platform } from "../../api/platform";
import type { Campus, OrganizationAiPermission, OrganizationDetail, OrganizationStatus } from "../../api/types";
import { Button, DataTable, ErrorState, Field, FormDialog, LoadingState, PageHeader, StatusBadge, useToast, type Column } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { formatDateTime } from "../../lib/format";

const CAMPUS_COLUMNS: Column<Campus>[] = [
  { key: "name", header: "Campus / branch", render: (c) => <strong>{c.name}</strong> },
  { key: "status", header: "Status", render: (c) => <StatusBadge status={c.status} /> },
  { key: "buildings", header: "Buildings", align: "right", render: (c) => c.building_count },
  { key: "perimeters", header: "Perimeters", align: "right", render: (c) => c.perimeter_count },
  { key: "floors", header: "Floors", align: "right", render: (c) => c.floor_count },
  { key: "areas", header: "Areas", align: "right", render: (c) => c.area_count },
  { key: "cameras", header: "Cameras", align: "right", render: (c) => c.camera_count },
  {
    key: "staff",
    header: "Active staff",
    render: (c) => (
      <span className="small">
        {c.admin_count} Admins · {c.supervisor_count} Supervisors · {c.guard_count} Guards
      </span>
    ),
  },
  { key: "created", header: "Approved", render: (c) => formatDateTime(c.created_at) },
];

const STATUS_ACTIONS: Array<[OrganizationStatus, string, string]> = [
  ["ACTIVE", "Activate", "Users can sign in and use the system again."],
  ["INACTIVE", "Deactivate", "Users cannot sign in; open sessions are refused at once."],
  ["SUSPENDED", "Suspend", "Like deactivation, recorded as a suspension (e.g. a policy or billing issue)."],
];

function PasswordField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label="Your current password" required hint="Re-enter your Super Admin password to confirm this change.">
      <input type="password" autoComplete="current-password" value={value} maxLength={1024} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

function EditAdminDialog({ organization, onClose, onSaved }: { organization: OrganizationDetail; onClose: () => void; onSaved: () => void }) {
  const admin = organization.main_admin;
  const [fullName, setFullName] = useState(admin?.full_name ?? "");
  const [email, setEmail] = useState(admin?.email ?? "");
  const [phone, setPhone] = useState(admin?.phone_number ?? "");
  const [password, setPassword] = useState("");
  const incomplete = !fullName.trim() || !email.trim() || !phone.trim() || !password;
  return (
    <FormDialog
      title={`Edit Main Admin of ${organization.name}`}
      submitLabel="Save contact details"
      onClose={onClose}
      disabled={incomplete}
      onSubmit={async () => {
        await platform.updateMainAdmin(organization.organization_id, {
          full_name: fullName.trim(),
          email: email.trim(),
          phone_number: phone.trim(),
          current_password: password,
        });
        onSaved();
      }}
    >
      <p className="dialog-message">The Admin's password is never shown and is not changed here.</p>
      <Field label="Full name" required>
        <input value={fullName} maxLength={120} onChange={(e) => setFullName(e.target.value)} />
      </Field>
      <Field label="Email" required hint="Must not belong to any other account.">
        <input type="email" value={email} maxLength={254} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Phone number" required hint="7–15 digits, optionally starting with +.">
        <input type="tel" value={phone} maxLength={40} onChange={(e) => setPhone(e.target.value)} />
      </Field>
      <PasswordField value={password} onChange={setPassword} />
    </FormDialog>
  );
}

function StatusDialog({ organization, onClose, onSaved }: { organization: OrganizationDetail; onClose: () => void; onSaved: (s: OrganizationStatus) => void }) {
  const choices = STATUS_ACTIONS.filter(([value]) => value !== organization.status);
  const [status, setStatus] = useState<OrganizationStatus>(choices[0][0]);
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  return (
    <FormDialog
      title={`Change the status of ${organization.name}`}
      submitLabel={`${STATUS_ACTIONS.find(([v]) => v === status)?.[1]} organization`}
      onClose={onClose}
      disabled={!password}
      onSubmit={async () => {
        await platform.changeStatus(organization.organization_id, {
          status,
          reason: reason.trim() || null,
          current_password: password,
        });
        onSaved(status);
      }}
    >
      <fieldset>
        <legend>New status (now {organization.status.toLowerCase()})</legend>
        {choices.map(([value, label, hint]) => (
          <label key={value} className="checkbox choice">
            <input type="radio" name="status" value={value} checked={status === value} onChange={() => setStatus(value)} />
            <span>
              {label}
              <span className="hint block">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <Field label="Reason" hint="Kept in the platform audit log.">
        <textarea value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <PasswordField value={password} onChange={setPassword} />
    </FormDialog>
  );
}

function AiPermissionDialog({
  organization,
  permission,
  onClose,
  onSaved,
}: {
  organization: OrganizationDetail;
  permission: OrganizationAiPermission;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [password, setPassword] = useState("");
  const block = !permission.blocked;
  return (
    <FormDialog
      title={`${block ? "Block" : "Unblock"} ${permission.label} for ${organization.name}`}
      submitLabel={block ? "Block feature" : "Unblock feature"}
      onClose={onClose}
      disabled={!password}
      onSubmit={async () => {
        await platform.changeAiPermission(organization.organization_id, {
          module_key: permission.module_key,
          blocked: block,
          current_password: password,
        });
        onSaved();
      }}
    >
      <p className="dialog-message">
        {block
          ? "It stops at once on every camera of this organization, and its Admins and Supervisors cannot switch it on."
          : "Its Admins and Supervisors can use it again. No camera is switched back on automatically: they choose the cameras."}
      </p>
      <PasswordField value={password} onChange={setPassword} />
    </FormDialog>
  );
}

/** AI Feature Access (Organization Controls, decision D-028): block or unblock a core vision module for one
 * organization, one feature per click, with the password. Audited. */
function AiAccessCard({ organization, onChange }: { organization: OrganizationDetail; onChange: (p: OrganizationAiPermission) => void }) {
  const core = (organization.ai_permissions ?? []).filter((p) => p.core);
  return (
    <section className="card" aria-labelledby="ai-access-title" data-testid="ai-access">
      <h2 id="ai-access-title">AI Feature Access (Organization Controls)</h2>
      <p className="muted small">
        Block a feature for this organization only. Platform-wide switches are on the AI Settings page; a feature disabled
        globally is off for everyone.
      </p>
      <ul className="ai-access-list">
        {core.map((p) => (
          <li key={p.module_key} className="ai-access-row">
            <span className="ai-access-name">
              <strong>{p.label}</strong>
              <StatusBadge status={p.status} />
            </span>
            <Button
              size="sm"
              variant={p.blocked ? "secondary" : "danger"}
              icon={p.blocked ? CircleCheck : Ban}
              aria-label={`${p.blocked ? "Unblock" : "Block"} ${p.label}`}
              onClick={() => onChange(p)}
            >
              {p.blocked ? "Unblock" : "Block"}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Organization Details (page 4): profile, campus breakdown (counts only), Main Admin, status. */
export function OrganizationDetailPage() {
  const { organizationId = "" } = useParams();
  const toast = useToast();
  const detail = useAsync(() => platform.organization(organizationId), [organizationId]);
  const [open, setOpen] = useState<"admin" | "status" | null>(null);
  const [aiChange, setAiChange] = useState<OrganizationAiPermission | null>(null);
  const back = { to: "/organizations", label: "Organizations" };

  if (detail.error)
    return (
      <section>
        <PageHeader title="Organization" back={back} />
        <ErrorState error={detail.error} onRetry={detail.reload} />
      </section>
    );
  if (!detail.data) return <LoadingState />;
  const o = detail.data;
  const admin = o.main_admin;

  return (
    <section>
      <PageHeader
        title={o.name}
        back={back}
        description={
          <>
            <span className="mono">{o.organization_id}</span> · <StatusBadge status={o.status} />
          </>
        }
        actions={
          <Button variant={o.status === "ACTIVE" ? "danger" : "primary"} icon={o.status === "ACTIVE" ? ShieldOff : Power} onClick={() => setOpen("status")}>
            Change status
          </Button>
        }
      />
      {o.status !== "ACTIVE" && (
        <p className="notice warning" role="status">
          This organization is {o.status.toLowerCase()}: its users cannot sign in or use the system.
        </p>
      )}
      <div className="grid-2">
        <section className="card" aria-labelledby="profile-title">
          <h2 id="profile-title">Organization profile</h2>
          <dl>
            <dt>Organization ID</dt>
            <dd className="mono">{o.organization_id}</dd>
            <dt>Official email</dt>
            <dd className="break-anywhere">{o.official_email ?? "—"}</dd>
            <dt>Phone number</dt>
            <dd>{o.official_phone_number ?? "—"}</dd>
            <dt>Registered address</dt>
            <dd className="muted">Not collected at registration</dd>
            <dt>Onboarded</dt>
            <dd>{formatDateTime(o.created_at)}</dd>
            <dt>Campuses / cameras</dt>
            <dd>
              {o.campus_count} / {o.camera_count}
            </dd>
          </dl>
        </section>
        <section className="card" aria-labelledby="admin-title">
          <div className="card-head">
            <h2 id="admin-title">Main Admin</h2>
            {admin && (
              <Button variant="secondary" size="sm" icon={Pencil} onClick={() => setOpen("admin")}>
                Edit contact
              </Button>
            )}
          </div>
          {admin ? (
            <dl>
              <dt>Full name</dt>
              <dd>{admin.full_name}</dd>
              <dt>Username</dt>
              <dd>{admin.username ?? "—"}</dd>
              <dt>Email</dt>
              <dd className="break-anywhere">{admin.email}</dd>
              <dt>Phone number</dt>
              <dd>{admin.phone_number ?? "—"}</dd>
            </dl>
          ) : (
            <p className="muted">This organization has no Main Admin.</p>
          )}
          <p className="hint">Passwords are never visible to the Super Admin.</p>
        </section>
      </div>
      {o.ai_permissions && o.ai_permissions.length > 0 && <AiAccessCard organization={o} onChange={setAiChange} />}
      <section className="card" aria-labelledby="campuses-title">
        <h2 id="campuses-title">Campuses and infrastructure</h2>
        <p className="muted small">Counts only. Camera feeds, recordings, alerts, chat and personnel are never available here.</p>
        <DataTable caption="Campuses" columns={CAMPUS_COLUMNS} rows={o.campuses} rowKey={(c) => c.name} empty="No campuses." />
      </section>
      {open === "admin" && (
        <EditAdminDialog
          organization={o}
          onClose={() => setOpen(null)}
          onSaved={() => {
            setOpen(null);
            toast("success", "Main Admin contact details saved.");
            detail.reload();
          }}
        />
      )}
      {aiChange && (
        <AiPermissionDialog
          organization={o}
          permission={aiChange}
          onClose={() => setAiChange(null)}
          onSaved={() => {
            setAiChange(null);
            toast("success", `${aiChange.label} is now ${aiChange.blocked ? "allowed" : "blocked"} for ${o.name}.`);
            detail.reload();
          }}
        />
      )}
      {open === "status" && (
        <StatusDialog
          organization={o}
          onClose={() => setOpen(null)}
          onSaved={(status) => {
            setOpen(null);
            toast("success", `${o.name} is now ${status.toLowerCase()}.`);
            detail.reload();
          }}
        />
      )}
    </section>
  );
}
