import { useState, type FormEvent } from "react";

import { download } from "../../../api/client";
import { imports } from "../api";
import type { ImportDetail } from "../types";
import { FileDown, Upload } from "lucide-react";

import { Button, ErrorState } from "../../../components/ui";

export function UploadStep({ onUploaded }: { onUploaded: (detail: ImportDetail) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [sheet, setSheet] = useState("");
  const [headerRow, setHeaderRow] = useState(1);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      onUploaded(await imports.upload(file, sheet, headerRow));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="card">
        <h2>1. Use the standard template (recommended)</h2>
        <p>
          The template matches this campus's categories and fields, marks required columns and explains every column. Other column
          names also work: you will match them to fields in the next step.
        </p>
        <div>
          <Button
            variant="secondary"
            icon={FileDown}
            onClick={() => download("/personnel-imports/template.xlsx", "personnel-import-template.xlsx").catch(setError)}
          >
            Download template (.xlsx)
          </Button>
        </div>
      </div>
      <form className="card stack" onSubmit={submit}>
        <h2>2. Upload the file</h2>
        {error ? <ErrorState error={error} /> : null}
        <label>
          Excel (.xlsx) or CSV (UTF-8) file
          <input type="file" accept=".xlsx,.csv" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <div className="row">
          <label>
            Sheet name (optional)
            <input value={sheet} onChange={(e) => setSheet(e.target.value)} placeholder="First sheet" />
          </label>
          <label>
            Header row
            <input type="number" min={1} value={headerRow} onChange={(e) => setHeaderRow(Number(e.target.value) || 1)} />
          </label>
        </div>
        <div>
          <Button type="submit" icon={Upload} disabled={!file} loading={busy}>
            Upload and read columns
          </Button>
        </div>
      </form>
    </div>
  );
}
