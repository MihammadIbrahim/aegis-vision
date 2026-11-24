import { CircleCheck, CircleX, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import {
  Button,
  DataTable,
  ErrorState,
  Field,
  FormDialog,
  PageHeader,
  Pagination,
  PasswordInput,
  StatusBadge,
  useToast,
  type Column,
} from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { StepUpDialog } from "../../account/components/StepUpDialog";
import { campusRequests, type CampusRequest, type CampusRequestRecord, type CampusRequestStatus } from "../api";

const PAGE = 25;

function Erased() {
  return <span className="muted">Erased on rejection</span>;
}

/** The Main Admin reviews requests to add a campus (Org Admin page 15). Approval needs the password and
 * a fresh code emailed to the Main Admin; rejection needs the password and erases the applicant's details. */
export function CampusRequestsPage() {
  const { session } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState<"pending" | "history">("pending");
  const [approving, setApproving] = useState<CampusRequest | null>(null);
  const [rejecting, setRejecting] = useState<CampusRequest | null>(null);
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<CampusRequestStatus | "">("");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(text);
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [text]);
  // Only the Main Admin may read requests; other Admins see a note and nothing is requested.
  const main = Boolean(session?.is_main_admin);
  const pending = useAsync(() => (main ? campusRequests.pending() : Promise.resolve([])), [main]);
  const history = useAsync(
    () =>
      main
        ? campusRequests.history({ status, q: q.trim() || undefined, limit: PAGE, offset })
        : Promise.resolve({ items: [], total: 0, limit: PAGE, offset: 0, counts: { PENDING: 0, APPROVED: 0, REJECTED: 0 } }),
    [main, status, q, offset],
  );

  if (!main) {
    return (
      <section>
        <PageHeader title="Campus Requests" />
        <p className="notice" role="note">
          Only the organization's Main Admin reviews requests to add a campus.
        </p>
      </section>
    );
  }

  function refresh() {
    pending.reload();
    history.reload();
  }

  const pendingColumns: Column<CampusRequest>[] = [
    { key: "campus", header: "Proposed campus", render: (r) => <strong>{r.proposed_campus_name}</strong> },
    {
      key: "applicant",
      header: "Applicant",
      render: (r) => (
        <div className="stack gap-0">
          <span>{r.applicant_name}</span>
          <span className="muted small break-anywhere">
            {r.applicant_email} · @{r.applicant_username ?? "—"} · {r.applicant_phone ?? "—"}
          </span>
        </div>
      ),
    },
    {
      key: "verified",
      header: "Email code",
      render: (r) => (r.email_verified ? `Verified ${formatDateTime(r.email_verified_at)}` : "Not verified"),
    },
    { key: "submitted", header: "Submitted", render: (r) => formatDateTime(r.submitted_at) },
    { key: "deadline", header: "Decide by", render: (r) => formatDateTime(r.decision_deadline) },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      render: (r) => (
        <div className="actions">
          <Button size="sm" icon={CircleCheck} disabled={!r.email_verified} onClick={() => setApproving(r)}>
            Approve
          </Button>
          <Button size="sm" variant="danger" icon={CircleX} onClick={() => setRejecting(r)}>
            Reject
          </Button>
        </div>
      ),
    },
  ];

  const historyColumns: Column<CampusRequestRecord>[] = [
    { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
    { key: "campus", header: "Proposed campus", render: (r) => (r.details_erased_at ? <Erased /> : r.proposed_campus_name) },
    {
      key: "applicant",
      header: "Applicant",
      render: (r) => (r.details_erased_at ? <Erased /> : `${r.applicant_name} · ${r.applicant_email}`),
    },
    { key: "submitted", header: "Submitted", render: (r) => formatDateTime(r.submitted_at) },
    {
      key: "decided",
      header: "Decided",
      render: (r) => (r.reviewed_at ? `${formatDateTime(r.reviewed_at)}${r.reviewed_by_name ? ` by ${r.reviewed_by_name}` : ""}` : "—"),
    },
  ];
  const counts = history.data?.counts;

  return (
    <section>
      <PageHeader
        title="Campus Requests"
        description="Requests from the “Already Registered Organization?” form. Only email-verified requests can be approved, within 72 hours of verification."
        actions={
          <Button variant="secondary" icon={RotateCw} onClick={refresh}>
            Refresh
          </Button>
        }
      />
      <div className="tabs" role="tablist" aria-label="Campus requests">
        <button type="button" role="tab" className={`tab${tab === "pending" ? " is-active" : ""}`} aria-selected={tab === "pending"} onClick={() => setTab("pending")}>
          Pending{pending.data ? ` (${pending.data.length})` : ""}
        </button>
        <button type="button" role="tab" className={`tab${tab === "history" ? " is-active" : ""}`} aria-selected={tab === "history"} onClick={() => setTab("history")}>
          History
        </button>
      </div>
      {tab === "pending" ? (
        pending.error ? (
          <ErrorState error={pending.error} onRetry={pending.reload} />
        ) : (
          <DataTable caption="Pending campus requests" columns={pendingColumns} rows={pending.data ?? []} rowKey={(r) => r.id} loading={pending.loading && !pending.data} empty="No requests are waiting for your decision." />
        )
      ) : (
        <>
          <div className="filters" role="search" aria-label="Find requests">
            <input type="search" aria-label="Search requests" placeholder="Applicant name, email or campus name" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
            <select
              aria-label="Status"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as CampusRequestStatus | "");
                setOffset(0);
              }}
            >
              <option value="">All statuses{counts ? ` (${counts.PENDING + counts.APPROVED + counts.REJECTED})` : ""}</option>
              <option value="PENDING">Pending{counts ? ` (${counts.PENDING})` : ""}</option>
              <option value="APPROVED">Approved{counts ? ` (${counts.APPROVED})` : ""}</option>
              <option value="REJECTED">Rejected{counts ? ` (${counts.REJECTED})` : ""}</option>
            </select>
          </div>
          {history.error ? (
            <ErrorState error={history.error} onRetry={history.reload} />
          ) : (
            <>
              <DataTable caption="Campus request history" columns={historyColumns} rows={history.data?.items ?? []} rowKey={(r) => r.id} loading={history.loading && !history.data} empty={q || status ? "No request matches." : "No requests yet."} />
              {history.data && (
                <Pagination offset={offset} limit={PAGE} total={history.data.total} onChange={setOffset} />
              )}
              <p className="muted small">Rejected requests keep no applicant details (they are erased when rejected).</p>
            </>
          )}
        </>
      )}
      {approving && (
        <StepUpDialog
          title={`Approve “${approving.proposed_campus_name}”`}
          intro={
            <>
              Approving creates the campus and gives <strong>{approving.applicant_name}</strong> an Admin account for it. Confirm
              with your password and the code we email you.
            </>
          }
          actionLabel="Approve campus"
          start={campusRequests.startAuthorization}
          resend={campusRequests.resendAuthorization}
          verify={campusRequests.verifyAuthorization}
          onConfirmed={async (authorizationId) => {
            await campusRequests.approve(approving.id, authorizationId);
            toast("success", `“${approving.proposed_campus_name}” approved. The applicant can now sign in.`);
            setApproving(null);
            refresh();
          }}
          onClose={() => setApproving(null)}
        />
      )}
      {rejecting && (
        <FormDialog
          title={`Reject “${rejecting.proposed_campus_name}”`}
          submitLabel="Reject and erase details"
          disabled={!password}
          onClose={() => {
            setRejecting(null);
            setPassword("");
          }}
          onSubmit={async () => {
            await campusRequests.reject(rejecting.id, password);
            toast("success", "Request rejected. The applicant's details were erased.");
            setRejecting(null);
            setPassword("");
            refresh();
          }}
        >
          <p className="dialog-message">
            The applicant's name, email, username, phone and the proposed campus name are erased permanently. This cannot
            be undone.
          </p>
          <Field label="Your current password" required>
            <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </FormDialog>
      )}
    </section>
  );
}
