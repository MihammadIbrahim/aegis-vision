import { useEffect, useState } from "react";

import { download } from "../../../api/client";
import { imports } from "../api";
import type { ImportDetail, ImportIssue, PreviewRow } from "../types";
import { Check, FileDown, Upload } from "lucide-react";

import { Button, DataTable, ErrorState, StatusBadge } from "../../../components/ui";

function IssueTable({ issues }: { issues: ImportIssue[] }) {
  return (
    <DataTable
      caption="Problems found"
      rows={issues.map((issue, index) => ({ ...issue, index }))}
      rowKey={(i) => i.index}
      columns={[
        { key: "row", header: "Row", render: (i) => i.row_number ?? "File" },
        { key: "column", header: "Column", render: (i) => i.column_name ?? "—" },
        { key: "severity", header: "Severity", render: (i) => <StatusBadge status={i.severity === "ERROR" ? "INVALID" : "RESTRICTED"} label={i.severity === "ERROR" ? "Error" : "Warning"} /> },
        { key: "problem", header: "Problem", render: (i) => i.message },
      ]}
    />
  );
}

export function ResultStep({
  detail,
  onConfirmed,
  onRemap,
  onRestart,
}: {
  detail: ImportDetail;
  onConfirmed: (d: ImportDetail) => void;
  onRemap: () => void;
  onRestart: () => void;
}) {
  const batch = detail.batch;
  const [issues, setIssues] = useState<ImportIssue[]>([]);
  const [preview, setPreview] = useState<PreviewRow[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    imports.issues(batch.id, { limit: 200 }).then(setIssues).catch(setError);
    if (batch.status === "VALIDATED") imports.preview(batch.id, 20).then(setPreview).catch(setError);
  }, [batch.id, batch.status]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      onConfirmed(await imports.confirm(batch.id, acknowledged));
    } catch (e) {
      setError(e);
      // The server validated again; show its new findings.
      imports.get(batch.id).then(onConfirmed).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  const annotated = () =>
    download(`/personnel-imports/${batch.id}/annotated-workbook`, `${batch.original_filename.replace(/\.[^.]+$/, "")}-validation.xlsx`).catch(
      setError,
    );

  return (
    <div className="card stack">
      <h2>4. Validation result</h2>
      {error ? <ErrorState error={error} /> : null}
      <div className="summary">
        <span>Rows checked: {batch.total_rows ?? 0}</span>
        <span>Valid rows: {batch.valid_rows ?? 0}</span>
        <span className="text-error">Blocking errors: {batch.error_count}</span>
        <span className="text-warning">Warnings: {batch.warning_count}</span>
      </div>
      {batch.failure_reason && <div className="notice warning">{batch.failure_reason}</div>}
      {batch.status === "INVALID" ? (
        <>
          <div className="notice error">
            Nothing will be imported while blocking errors remain. Download the marked-up copy of your file (problem cells in red,
            warnings in amber, with comments), fix it, and upload the corrected file.
          </div>
          <div className="actions">
            {batch.has_annotated_workbook && (
              <Button icon={FileDown} onClick={annotated}>
                Download error workbook
              </Button>
            )}
            <Button variant="secondary" icon={Upload} onClick={onRestart}>
              Upload corrected file
            </Button>
            <Button variant="ghost" onClick={onRemap}>
              Change the column mapping
            </Button>
          </div>
        </>
      ) : (
        <>
          <h3>Preview (first rows)</h3>
          <DataTable
            caption="Preview of the first rows"
            rows={preview}
            rowKey={(p) => p.row_number}
            empty="No rows to preview."
            columns={[
              { key: "row", header: "Row", align: "right", render: (p) => p.row_number },
              { key: "name", header: "Name", render: (p) => String(p.values.full_name ?? "") },
              { key: "category", header: "Category", render: (p) => p.category },
              { key: "external", header: "Institutional ID", render: (p) => String(p.values.external_id ?? "—") },
              { key: "email", header: "Email", render: (p) => String(p.values.email ?? "—") },
            ]}
          />
          {batch.warning_count > 0 && (
            <>
              <div className="actions">
                {batch.has_annotated_workbook && (
                  <Button variant="secondary" icon={FileDown} onClick={annotated}>
                    Download workbook with warnings
                  </Button>
                )}
              </div>
              <label className="checkbox">
                <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />I have reviewed the{" "}
                {batch.warning_count} warning(s) and want to import anyway.
              </label>
            </>
          )}
          <div>
            <Button icon={Check} onClick={confirm} loading={busy} disabled={batch.warning_count > 0 && !acknowledged}>
              {`Import all ${batch.valid_rows ?? 0} records`}
            </Button>
          </div>
        </>
      )}
      {issues.length > 0 && (
        <>
          <h3>Problems found{issues.length >= 200 ? " (first 200; the workbook lists all)" : ""}</h3>
          <IssueTable issues={issues} />
        </>
      )}
    </div>
  );
}
