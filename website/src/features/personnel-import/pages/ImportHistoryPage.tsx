import { Archive, FileDown, Plus, Trash2, Undo2 } from "lucide-react";
import { Link } from "react-router-dom";

import { download } from "../../../api/client";
import { Button, DataTable, ErrorState, PageHeader, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatDateTime } from "../../../lib/format";
import { imports, type MappingProfile } from "../api";
import type { ImportBatch } from "../types";

const UNCONFIRMED = ["UPLOADED", "INVALID", "VALIDATED"];

function fieldName(target: string): string {
  return target === "IGNORE" ? "ignored" : target.replace(/_/g, " ").toLowerCase();
}

export function ImportHistoryPage() {
  const history = useAsync(() => imports.history(), []);
  const profiles = useAsync(() => imports.profiles(), []);
  const toast = useToast();
  const { confirm, dialog } = useConfirm();

  async function act(action: () => Promise<unknown>, done: string) {
    try {
      await action();
      toast("success", done);
      history.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  async function rollBack(b: ImportBatch) {
    const answer = await confirm({
      title: `Roll back ${b.original_filename}?`,
      message: `Its ${b.imported_count} records will be deactivated (kept for history) and their restricted-area access revoked.`,
      confirmLabel: "Roll back import",
      tone: "danger",
      reason: { label: "Reason", maxLength: 400 },
    });
    if (answer.confirmed) await act(() => imports.rollback(b.id, answer.reason), "Import rolled back.");
  }

  async function deleteProfile(p: MappingProfile) {
    const answer = await confirm({
      title: `Delete the saved mapping "${p.name}"?`,
      message: "Later uploads no longer use it for suggestions. Imports already made are not affected.",
      confirmLabel: "Delete mapping",
      tone: "danger",
    });
    if (!answer.confirmed) return;
    try {
      await imports.deleteProfile(p.id);
      toast("success", "Saved mapping deleted.");
      profiles.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  const profileColumns: Column<MappingProfile>[] = [
    { key: "name", header: "Name", render: (p) => <strong>{p.name}</strong> },
    {
      key: "mapping",
      header: "Columns",
      render: (p) => (
        <span className="small">
          {Object.entries(p.mapping)
            .map(([header, target]) => `${header} → ${fieldName(target)}`)
            .join(" · ")}
        </span>
      ),
    },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      render: (p) => (
        <Button size="sm" variant="ghost" icon={Trash2} onClick={() => void deleteProfile(p)}>
          Delete
        </Button>
      ),
    },
  ];

  async function discard(b: ImportBatch) {
    const answer = await confirm({
      title: `Discard ${b.original_filename}?`,
      message: "Nothing was imported from it. The uploaded file is erased; the history entry stays.",
      confirmLabel: "Discard",
      tone: "danger",
    });
    if (answer.confirmed) await act(() => imports.discard(b.id), "Import discarded.");
  }

  const columns: Column<ImportBatch>[] = [
    { key: "file", header: "File", render: (b) => <Link to={`/personnel/imports/${b.id}`}>{b.original_filename}</Link> },
    {
      key: "status",
      header: "Status",
      render: (b) => (
        <>
          <StatusBadge status={b.status} />
          {b.failure_reason && <div className="small muted">{b.failure_reason}</div>}
        </>
      ),
    },
    { key: "rows", header: "Rows", align: "right", render: (b) => b.total_rows ?? "—" },
    { key: "issues", header: "Errors / warnings", align: "right", render: (b) => `${b.error_count} / ${b.warning_count}` },
    {
      key: "imported",
      header: "Imported",
      align: "right",
      render: (b) => `${b.imported_count}${b.rolled_back_count > 0 ? ` (${b.rolled_back_count} rolled back)` : ""}`,
    },
    {
      key: "uploaded",
      header: "Uploaded",
      render: (b) => (
        <>
          {formatDateTime(b.uploaded_at)}
          <div className="small muted">{b.uploaded_by}</div>
        </>
      ),
    },
    {
      key: "confirmed",
      header: "Confirmed",
      render: (b) => (
        <>
          {formatDateTime(b.confirmed_at)}
          <div className="small muted">{b.confirmed_by}</div>
        </>
      ),
    },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      render: (b) => (
        <div className="actions">
          {b.has_annotated_workbook && b.files_available && (
            <Button
              variant="ghost"
              size="sm"
              icon={FileDown}
              onClick={() => void download(`/personnel-imports/${b.id}/annotated-workbook`, "validation.xlsx").catch((e) => toast("error", String(e.message ?? e)))}
            >
              Error workbook
            </Button>
          )}
          {b.status === "IMPORTED" && (
            <Button variant="ghost" size="sm" icon={Undo2} onClick={() => void rollBack(b)}>
              Roll back
            </Button>
          )}
          {UNCONFIRMED.includes(b.status) && (
            <Button variant="ghost" size="sm" icon={Archive} onClick={() => void discard(b)}>
              Discard
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <section>
      <PageHeader
        title="Import history"
        actions={
          <Link className="button" to="/personnel/imports/new">
            <Plus className="icon" aria-hidden="true" />
            New import
          </Link>
        }
      />
      {history.error ? (
        <ErrorState error={history.error} onRetry={history.reload} />
      ) : (
        <DataTable
          caption="Import history"
          columns={columns}
          rows={history.data ?? []}
          rowKey={(b) => b.id}
          loading={history.loading && !history.data}
          empty="No imports yet."
        />
      )}
      <section className="card" aria-labelledby="profiles-title">
        <h2 id="profiles-title">Saved column mappings</h2>
        <p className="muted small">Saved when you confirm a mapping with a name; later uploads with the same headers are matched automatically.</p>
        {profiles.error ? (
          <ErrorState error={profiles.error} onRetry={profiles.reload} />
        ) : (
          <DataTable
            caption="Saved column mappings"
            columns={profileColumns}
            rows={profiles.data ?? []}
            rowKey={(p) => p.id}
            loading={profiles.loading && !profiles.data}
            empty="No saved mappings."
          />
        )}
      </section>
      {dialog}
    </section>
  );
}
