import { CircleCheck } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";

import { Button, ErrorState, LoadingState, PageHeader, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatDateTime, formatLocationPath } from "../../../lib/format";
import { manualReports } from "../api";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children ?? "—"}</dd>
    </>
  );
}

export function ReportDetailPage() {
  const { id = "" } = useParams();
  const toast = useToast();
  const { confirm, dialog } = useConfirm();
  const report = useAsync(() => manualReports.get(id), [id]);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoError, setPhotoError] = useState(false);
  const [busy, setBusy] = useState(false);
  const hasPhoto = Boolean(report.data?.has_photo);

  // The photo is fetched with the session's token and never cached by the browser.
  useEffect(() => {
    if (!hasPhoto) return;
    let url: string | null = null;
    let live = true;
    setPhotoError(false);
    manualReports
      .photo(id)
      .then((blob) => {
        if (!live) return;
        url = URL.createObjectURL(blob);
        setPhotoUrl(url);
      })
      .catch(() => live && setPhotoError(true));
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, hasPhoto]);

  async function review() {
    const answer = await confirm({
      title: "Mark this report as reviewed?",
      message: "The Guard's report stays unchanged; your review is recorded in the audit log.",
      confirmLabel: "Mark as reviewed",
      reason: { label: "Review note", maxLength: 1000 },
    });
    if (!answer.confirmed) return;
    setBusy(true);
    try {
      await manualReports.review(id, answer.reason);
      toast("success", "Report marked as reviewed.");
      report.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!report.data) return report.error ? <ErrorState error={report.error} onRetry={report.reload} /> : <LoadingState />;
  const r = report.data;
  return (
    <section>
      <PageHeader
        back={{ to: "/manual-reports", label: "Manual Reports" }}
        title={
          <>
            {r.report_type} <StatusBadge status={r.status} />
          </>
        }
        description="Manual report from a Guard (not an AI alert)."
        actions={
          r.status === "SUBMITTED" && (
            <Button icon={CircleCheck} loading={busy} onClick={() => void review()}>
              Mark as reviewed
            </Button>
          )
        }
      />
      <div className="grid-2">
        <section className="card" aria-labelledby="report-title">
          <h2 id="report-title">Report</h2>
          <dl>
            <Row label="Report ID">
              <span className="mono">{r.id}</span>
            </Row>
            <Row label="Submitted by">
              {r.guard_name} <span className="mono muted">{r.guard_id}</span>
            </Row>
            <Row label="Location">{formatLocationPath(r.building_name, r.floor_name, r.area_name)}</Row>
            <Row label="Occurred">{r.occurred_at ? formatDateTime(r.occurred_at) : "Not given"}</Row>
            <Row label="Received">{formatDateTime(r.created_at)}</Row>
            {r.status === "REVIEWED" && <Row label="Reviewed">{formatDateTime(r.reviewed_at)}</Row>}
            {r.status === "REVIEWED" && <Row label="Review note">{r.review_note ?? "—"}</Row>}
          </dl>
          <h3>Description</h3>
          <p className="prewrap">{r.description}</p>
        </section>
        <section className="card" aria-labelledby="photo-title">
          <h2 id="photo-title">Photo</h2>
          {!r.has_photo ? (
            <p className="muted">The Guard attached no photo.</p>
          ) : photoError ? (
            <p className="notice error" role="alert">
              The photo could not be loaded.
            </p>
          ) : photoUrl ? (
            <img className="report-photo" src={photoUrl} alt={`Photo attached by ${r.guard_name} to the ${r.report_type} report`} />
          ) : (
            <LoadingState label="Loading photo…" />
          )}
        </section>
      </div>
      {dialog}
    </section>
  );
}
