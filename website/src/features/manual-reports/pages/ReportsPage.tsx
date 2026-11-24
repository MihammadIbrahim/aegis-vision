import { Image, RotateCw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Button, DataTable, ErrorState, PageHeader, Pagination, StatusBadge, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime, formatLocationPath } from "../../../lib/format";
import { manualReports } from "../api";
import type { ManualReport } from "../types";

const PAGE = 25;

const COLUMNS: Column<ManualReport>[] = [
  { key: "type", header: "Report", render: (r) => <Link to={`/manual-reports/${r.id}`}>{r.report_type}</Link> },
  {
    key: "guard",
    header: "Submitted by",
    render: (r) => (
      <>
        {r.guard_name} <span className="mono muted">{r.guard_id}</span>
      </>
    ),
  },
  { key: "location", header: "Location", render: (r) => formatLocationPath(r.building_name, r.floor_name, r.area_name) },
  { key: "occurred", header: "Occurred", render: (r) => formatDateTime(r.occurred_at) },
  { key: "received", header: "Received", render: (r) => formatDateTime(r.created_at) },
  {
    key: "photo",
    header: "Photo",
    render: (r) =>
      r.has_photo ? (
        <span className="inline-icon">
          <Image className="icon" aria-hidden="true" />
          Yes
        </span>
      ) : (
        "No"
      ),
  },
  { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
];

/** Manual security reports filed by this campus's Guards, newest first. Kept apart from AI alerts. */
export function ReportsPage() {
  const [offset, setOffset] = useState(0);
  const list = useAsync(() => manualReports.list(PAGE + 1, offset), [offset]);
  const rows = (list.data ?? []).slice(0, PAGE);
  return (
    <section>
      <PageHeader
        title="Manual Reports"
        description="Reports written by this campus's Guards in the mobile app. They are not AI alerts."
        actions={
          <Button variant="secondary" icon={RotateCw} onClick={list.reload} loading={list.loading && Boolean(list.data)}>
            Refresh
          </Button>
        }
      />
      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : (
        <>
          <DataTable
            caption="Manual reports"
            columns={COLUMNS}
            rows={rows}
            rowKey={(r) => r.id}
            loading={list.loading && !list.data}
            empty="No manual reports yet."
          />
          {list.data && (offset > 0 || list.data.length > PAGE) && (
            <Pagination count={rows.length} hasMore={list.data.length > PAGE} limit={PAGE} offset={offset} onChange={setOffset} />
          )}
        </>
      )}
    </section>
  );
}
