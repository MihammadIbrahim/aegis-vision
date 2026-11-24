import { Download, Send, Trash2, UserCheck, UserX } from "lucide-react";
import { useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { Button, DataTable, ErrorState, Field, FormDialog, LoadingState, PageHeader, PasswordInput, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { account, organization, type DeletionStarted } from "../api";
import { StepUpDialog } from "../components/StepUpDialog";

const BLOCKED: Record<string, string> = {
  OWNERSHIP_TRANSFER_REQUIRED: "As Main Admin you must first hand ownership to another Admin.",
  OWNERSHIP_TRANSFER_PENDING: "Your ownership transfer is waiting for the other Admin.",
  OWNERSHIP_SUCCESSOR_REQUIRED: "No other Admin with a password can take over yet.",
};

/** The Admin's account: the organization's Admin directory, Main Admin ownership transfer (sent, received,
 * history) and Delete Account with an optional export (Org Admin pages 14 and 24). */
export function AccountPage() {
  const { session, signOut } = useAuth();
  const toast = useToast();
  const status = useAsync(() => account.deletionStatus(), []);
  const incoming = useAsync(() => account.incoming(), []);
  const history = useAsync(() => (session?.is_main_admin ? account.history() : Promise.resolve([])), [session?.is_main_admin]);
  const directory = useAsync(() => organization.directory(), []);
  const [transferTo, setTransferTo] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteForm, setDeleteForm] = useState({ admin_id: "", password: "" });
  const [started, setStarted] = useState<DeletionStarted | null>(null);
  const [otp, setOtp] = useState("");
  const reload = () => {
    status.reload();
    incoming.reload();
    history.reload();
  };
  const fail = (e: unknown) => toast("error", e instanceof Error ? e.message : String(e));

  if (status.error) return <ErrorState error={status.error} onRetry={status.reload} />;
  if (!status.data) return <LoadingState />;
  const s = status.data;
  return (
    <section>
      <PageHeader title="Account & Organization" description="Admins of the organization, Main Admin ownership, and deleting your account." />
      <div className="grid-2">
        <section className="card" aria-labelledby="acct-directory">
          <h2 id="acct-directory">Admins of the organization</h2>
          {directory.error ? (
            <ErrorState error={directory.error} onRetry={directory.reload} />
          ) : (
            <ul className="plain stack">
              {(directory.data?.admins ?? []).map((a) => (
                <li key={`${a.full_name}-${a.is_first_admin}`}>
                  {a.full_name} {a.is_first_admin && <StatusBadge status="APPROVED" />} {a.is_first_admin && <span className="muted small">Main Admin</span>}
                </li>
              ))}
            </ul>
          )}
          <p className="muted small">Each campus's data stays with that campus; this list shows names only.</p>
        </section>
        <section className="card" aria-labelledby="acct-transfer">
          <h2 id="acct-transfer">Main Admin ownership</h2>
          {s.is_main_admin ? (
            <>
              <p className="small">You are the Main Admin. You can hand ownership to another Admin; they must accept it.</p>
              {s.latest_transfer && (
                <p className="notice">
                  Latest transfer to <strong>{s.latest_transfer.to_name}</strong>: {s.latest_transfer.status.replace(/_/g, " ").toLowerCase()} (until {formatDateTime(s.latest_transfer.expires_at)}).{" "}
                  {["AWAITING_VERIFICATION", "PENDING_ACCEPTANCE"].includes(s.latest_transfer.status) && (
                    <Button size="sm" variant="ghost" onClick={() => account.cancelTransfer(s.latest_transfer!.transfer_id).then(reload, fail)}>
                      Cancel transfer
                    </Button>
                  )}
                </p>
              )}
              <ul className="plain stack">
                {s.transfer_candidates.map((c) => (
                  <li key={c.admin_id} className="row">
                    <span>
                      {c.full_name} <span className="muted small mono">{c.admin_id}</span>
                    </span>
                    <Button size="sm" variant="secondary" icon={Send} onClick={() => setTransferTo(c.admin_id)}>
                      Offer ownership
                    </Button>
                  </li>
                ))}
                {s.transfer_candidates.length === 0 && <li className="muted small">No other Admin can take over yet.</li>}
              </ul>
            </>
          ) : (
            <p className="small">You are a campus Admin.</p>
          )}
          {(incoming.data ?? []).length > 0 && (
            <>
              <h3 className="dash-subhead">Offers to you</h3>
              <ul className="plain stack">
                {(incoming.data ?? []).map((t) => (
                  <li key={t.transfer_id} className="row">
                    <span>
                      From <strong>{t.from_name}</strong>, until {formatDateTime(t.expires_at)}
                    </span>
                    <Button size="sm" icon={UserCheck} onClick={() => account.accept(t.transfer_id).then(() => { toast("success", "You are now the Main Admin."); reload(); }, fail)}>
                      Accept
                    </Button>
                    <Button size="sm" variant="secondary" icon={UserX} onClick={() => account.reject(t.transfer_id).then(reload, fail)}>
                      Decline
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>
      {s.is_main_admin && (
        <section className="card" aria-labelledby="acct-history">
          <h2 id="acct-history">Ownership history</h2>
          <DataTable
            caption="Accepted ownership transfers"
            columns={[
              { key: "summary", header: "Transfer", render: (h) => h.summary },
              { key: "previous", header: "Previous owner", render: (h) => `${h.previous_owner_name} · ${h.previous_owner_email}` },
              { key: "accepted", header: "Accepted", render: (h) => formatDateTime(h.accepted_at) },
            ]}
            rows={history.data ?? []}
            rowKey={(h) => h.transfer_id}
            empty="No ownership has been transferred to you."
          />
        </section>
      )}
      <section className="card" aria-labelledby="acct-delete">
        <h2 id="acct-delete">Delete your account</h2>
        <p className="small">
          {s.organization_will_be_deleted
            ? "You are the only Admin: deleting your account deletes the whole organization."
            : s.campus_will_be_deleted
              ? "You are your campus's only Admin: its staff, cameras, alerts and records are deleted with your account."
              : "Your account is removed; your campus stays with its other Admins."}
        </p>
        {s.blocked_by && <p className="notice warning">{BLOCKED[s.blocked_by] ?? s.blocked_by}</p>}
        <Button variant="danger" icon={Trash2} disabled={!s.can_delete} onClick={() => setDeleting(true)}>
          Delete my account
        </Button>
      </section>

      {transferTo && (
        <StepUpDialog
          title="Offer Main Admin ownership"
          intro={<>The Admin <span className="mono">{transferTo}</span> must accept within the time shown. Confirm with your password and a code.</>}
          actionLabel="Send offer"
          start={(password) => account.startTransfer(transferTo, password)}
          resend={account.resendTransfer}
          verify={account.verifyTransfer}
          onConfirmed={async () => {
            toast("success", "The offer was sent. It applies once accepted.");
            setTransferTo(null);
            reload();
          }}
          onClose={() => setTransferTo(null)}
        />
      )}
      {deleting && !started && (
        <FormDialog
          title="Delete your account"
          submitLabel="Send code"
          disabled={!deleteForm.admin_id || !deleteForm.password}
          onClose={() => setDeleting(false)}
          onSubmit={async () => setStarted(await account.startDeletion(deleteForm.admin_id.trim(), deleteForm.password))}
        >
          <p className="dialog-message">Type your Admin ID and password. We then email you a code to confirm.</p>
          <Field label="Your Admin ID" required>
            <input value={deleteForm.admin_id} maxLength={40} onChange={(e) => setDeleteForm({ ...deleteForm, admin_id: e.target.value })} />
          </Field>
          <Field label="Your current password" required>
            <PasswordInput value={deleteForm.password} autoComplete="current-password" onChange={(e) => setDeleteForm({ ...deleteForm, password: e.target.value })} />
          </Field>
        </FormDialog>
      )}
      {started && (
        <FormDialog
          title="Confirm the deletion"
          submitLabel="Delete permanently"
          disabled={!/^[0-9]{6}$/.test(otp)}
          onClose={() => {
            setStarted(null);
            setDeleting(false);
          }}
          onSubmit={async () => {
            await account.completeDeletion(started.id, otp);
            toast("success", "Your account was deleted.");
            signOut();
          }}
        >
          <p className="dialog-message">
            Before you confirm you may download a copy of your campus's records (optional). Not included: {started.export.not_included.join(", ").replace(/_/g, " ")}.
          </p>
          <Button variant="secondary" icon={Download} onClick={() => account.exportCampus(started.id).catch(fail)}>
            Download a copy
          </Button>
          <Field label={`Code sent to ${started.email}`} required>
            <input inputMode="numeric" maxLength={6} value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} />
          </Field>
        </FormDialog>
      )}
    </section>
  );
}
