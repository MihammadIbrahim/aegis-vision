import { IdCard, KeyRound, MapPin, Pencil, Power, PowerOff, Radio } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { useAuth } from "../../../auth/AuthContext";
import { canAdministerStaff } from "../../../auth/permissions";
import { Button, ErrorState, Field, FormDialog, LoadingState, PageHeader, PasswordInput, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatDateTime, formatLocationPath } from "../../../lib/format";
import { userRecords } from "../../user-records/api";
import { staff } from "../api";
import { AssignLocationDialog, EditGuardDialog, SetPasswordDialog, StatusDialog } from "../components/StaffDialogs";
import { CATEGORY_TEXT, ROLE_LABEL } from "../labels";

type Open = "edit" | "password" | "status" | "location" | "new-id" | null;

/** A new Guard ID for an active Guard (Admins and Supervisors), confirmed with the caller's own password. */
function NewGuardIdDialog({ guardId, name, oldId, onClose, onSaved }: { guardId: string; name: string; oldId: string; onClose: () => void; onSaved: (newId: string) => void }) {
  const [password, setPassword] = useState("");
  return (
    <FormDialog
      title={`Give ${name} a new Guard ID`}
      submitLabel="Generate new ID"
      disabled={!password}
      onClose={onClose}
      onSubmit={async () => onSaved((await userRecords.regenerateGuardId(guardId, password)).display_id)}
    >
      <p className="dialog-message">
        {oldId} stops working for sign-in and a new Guard ID is generated. The Guard is notified of the new ID; history, alerts and reports
        stay linked to the same account. Supervisor IDs cannot be changed.
      </p>
      <Field label="Your current password" required>
        <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
    </FormDialog>
  );
}

/** One Guard (Admins and Supervisors) or Supervisor (Admins). */
export function StaffMemberPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const admin = canAdministerStaff(session?.role);
  const toast = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const member = useAsync(() => staff.get(id), [id]);
  const [open, setOpen] = useState<Open>(null);
  const back = { to: member.data?.role === "SUPERVISOR" ? "/staff?role=SUPERVISOR" : "/staff", label: "Staff Management" };

  if (member.error) {
    return (
      <section>
        <PageHeader title="Staff member" back={back} />
        <ErrorState error={member.error} onRetry={member.reload} />
      </section>
    );
  }
  if (!member.data) return <LoadingState />;
  const m = member.data;
  const guard = m.role === "GUARD";
  const active = m.status === "ACTIVE";
  const label = ROLE_LABEL[m.role];

  function done(message: string) {
    setOpen(null);
    toast("success", message);
    member.reload();
  }

  async function makeStandby() {
    const { confirmed } = await confirm({
      title: `Make ${m.full_name} a Standby Guard?`,
      message:
        "The Guard's post is removed. Standby Guards receive alerts only through escalation and campus-wide dispatch. The Guard is signed out of the app at once and signs in again in Standby mode.",
      confirmLabel: "Make Standby",
    });
    if (!confirmed) return;
    try {
      await staff.unassignGuard(m.display_id);
      toast("success", `${m.full_name} is now a Standby Guard.`);
      member.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section>
      <PageHeader
        title={m.full_name}
        back={back}
        description={
          <>
            {label} · <span className="mono">{m.display_id}</span>
          </>
        }
        actions={
          <>
            {guard && active && (
              <>
                <Button variant="secondary" icon={Pencil} onClick={() => setOpen("edit")}>
                  Edit details
                </Button>
                <Button variant="secondary" icon={KeyRound} onClick={() => setOpen("password")}>
                  Set password
                </Button>
                <Button variant="secondary" icon={IdCard} onClick={() => setOpen("new-id")}>
                  New Guard ID
                </Button>
              </>
            )}
            {admin && (
              <Button variant={active ? "danger" : "primary"} icon={active ? PowerOff : Power} onClick={() => setOpen("status")}>
                {active ? "Deactivate" : "Reactivate"}
              </Button>
            )}
          </>
        }
      />
      {!active && (
        <p className="notice warning" role="status">
          This account is deactivated (status "Left"): it cannot sign in.{admin ? " Reactivate it to make changes." : ""}
        </p>
      )}
      <div className="grid-2">
        {guard && m.category && (
          <section className="card" aria-labelledby="category-title">
            <div className="card-head">
              <h2 id="category-title">Operational category</h2>
              <StatusBadge status={m.category} />
            </div>
            <p>{CATEGORY_TEXT[m.category].description}</p>
            <dl>
              <dt>Post</dt>
              <dd>
                {m.location ? (
                  formatLocationPath(m.location.building_name, m.location.floor_name, m.location.area_name)
                ) : (
                  <span className="muted">None: campus-wide</span>
                )}
              </dd>
            </dl>
            {active && (
              <div className="actions">
                <Button variant="secondary" icon={MapPin} onClick={() => setOpen("location")}>
                  {m.location ? "Change location" : "Assign location"}
                </Button>
                {m.location && (
                  <Button variant="ghost" icon={Radio} onClick={makeStandby}>
                    Make Standby
                  </Button>
                )}
              </div>
            )}
          </section>
        )}
        <section className="card" aria-labelledby="account-title">
          <h2 id="account-title">Account</h2>
          <dl>
            <dt>{label} ID</dt>
            <dd className="mono">{m.display_id}</dd>
            <dt>Status</dt>
            <dd>
              <StatusBadge status={m.status} />
            </dd>
            <dt>Username</dt>
            <dd>{m.username ?? "—"}</dd>
            <dt>Email</dt>
            <dd className="break-anywhere">{m.email}</dd>
            <dt>Phone number</dt>
            <dd>{m.phone_number ?? "—"}</dd>
            <dt>Created</dt>
            <dd>{formatDateTime(m.created_at)}</dd>
          </dl>
          {!admin && <p className="hint">Changes you make here are recorded and reported to the Organization Admins.</p>}
        </section>
      </div>
      {open === "edit" && <EditGuardDialog guard={m} onClose={() => setOpen(null)} onSaved={() => done("Changes saved.")} />}
      {open === "password" && (
        <SetPasswordDialog guard={m} onClose={() => setOpen(null)} onSaved={() => done(`New password set for ${m.full_name}.`)} />
      )}
      {open === "status" && (
        <StatusDialog
          member={m}
          onClose={() => setOpen(null)}
          onSaved={(changed) => done(`${changed.full_name} ${changed.status === "ACTIVE" ? "reactivated" : "deactivated"}.`)}
        />
      )}
      {open === "new-id" && (
        <NewGuardIdDialog guardId={m.id} name={m.full_name} oldId={m.display_id} onClose={() => setOpen(null)} onSaved={(newId) => done(`${m.full_name}'s new Guard ID is ${newId}.`)} />
      )}
      {open === "location" && <AssignLocationDialog guard={m} onClose={() => setOpen(null)} onSaved={() => done("Location saved.")} />}
      {confirmDialog}
    </section>
  );
}
