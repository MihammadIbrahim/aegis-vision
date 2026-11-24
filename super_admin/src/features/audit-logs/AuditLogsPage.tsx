import { Download, RotateCw, Search } from "lucide-react";
import { useState, type FormEvent } from "react";

import { platform } from "../../api/platform";
import type { AuditQuery } from "../../api/types";
import { AuditTable } from "../../components/common/AuditTable";
import { Button, ErrorState, PageHeader, Pagination, useToast } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { humanize } from "../../lib/format";

export const AUDIT_PAGE = 50;

interface Draft {
  organization: string;
  action: string;
  outcome: "" | "SUCCESS" | "FAILURE";
  date_from: string;
  date_to: string;
}

const EMPTY: Draft = { organization: "", action: "", outcome: "", date_from: "", date_to: "" };

function toQuery(d: Draft): AuditQuery {
  return {
    organization: d.organization.trim() || undefined,
    action: d.action || undefined,
    outcome: d.outcome || undefined,
    date_from: d.date_from || undefined,
    date_to: d.date_to || undefined,
  };
}

/** Platform Audit Logs (page 7): filters, paging, CSV export. Never shows secrets. */
export function AuditLogsPage() {
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [query, setQuery] = useState<AuditQuery>({});
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const actions = useAsync(() => platform.auditActions(), []);
  const page = useAsync(() => platform.auditLogs({ ...query, limit: AUDIT_PAGE, offset }), [query, offset]);
  const badRange = Boolean(draft.date_from && draft.date_to && draft.date_to < draft.date_from);

  function apply(event: FormEvent) {
    event.preventDefault();
    if (badRange) return;
    setOffset(0);
    setQuery(toQuery(draft));
  }

  async function exportCsv() {
    setExporting(true);
    try {
      await platform.exportAuditLogs(query);
      toast("success", "Audit log exported.");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <section>
      <PageHeader
        title="Platform audit logs"
        description="Platform-level events: Super Admin sign-ins and changes, organization registrations, ownership transfers and deletions. Passwords, codes and tokens are never recorded."
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={page.reload} loading={page.loading && Boolean(page.data)}>
              Refresh
            </Button>
            <Button variant="secondary" icon={Download} onClick={exportCsv} loading={exporting}>
              Export CSV
            </Button>
          </>
        }
      />
      <form className="filters" onSubmit={apply} aria-label="Filter audit logs">
        <input
          aria-label="Organization"
          placeholder="Organization ID or name"
          value={draft.organization}
          maxLength={200}
          onChange={(e) => setDraft({ ...draft, organization: e.target.value })}
        />
        <select aria-label="Action" value={draft.action} onChange={(e) => setDraft({ ...draft, action: e.target.value })}>
          <option value="">All actions</option>
          {(actions.data ?? []).map((a) => (
            <option key={a.action} value={a.action}>
              {humanize(a.action)} ({a.count})
            </option>
          ))}
        </select>
        <select aria-label="Outcome" value={draft.outcome} onChange={(e) => setDraft({ ...draft, outcome: e.target.value as Draft["outcome"] })}>
          <option value="">Any outcome</option>
          <option value="SUCCESS">Success</option>
          <option value="FAILURE">Failure</option>
        </select>
        <label className="inline-field">
          From
          <input type="date" aria-label="From date" value={draft.date_from} onChange={(e) => setDraft({ ...draft, date_from: e.target.value })} />
        </label>
        <label className="inline-field">
          To
          <input type="date" aria-label="To date" value={draft.date_to} onChange={(e) => setDraft({ ...draft, date_to: e.target.value })} />
        </label>
        <Button type="submit" icon={Search} disabled={badRange}>
          Apply
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setDraft(EMPTY);
            setOffset(0);
            setQuery({});
          }}
        >
          Clear
        </Button>
      </form>
      {badRange && (
        <p className="field-error" role="alert">
          The end date is before the start date.
        </p>
      )}
      <p className="muted small">Dates are in UTC; the end date includes that whole day. Export uses the applied filters.</p>
      {page.error ? (
        <ErrorState error={page.error} onRetry={page.reload} />
      ) : (
        <>
          <AuditTable
            entries={page.data?.items ?? []}
            caption="Platform audit log"
            loading={page.loading && !page.data}
            empty={Object.values(query).some(Boolean) ? "No events match." : "No platform events yet."}
          />
          {page.data && page.data.total > 0 && <Pagination total={page.data.total} limit={AUDIT_PAGE} offset={offset} onChange={setOffset} />}
        </>
      )}
    </section>
  );
}
