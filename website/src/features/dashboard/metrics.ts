// Pure calculations behind every dashboard figure. Each takes real API rows and says whether the
// result is complete, so a count is never shown as exact when the list was cut off.
import type { Alert, AlertStatus, AlertType } from "../alerts";
import type { BuildingNode } from "../locations";
import type { ManualReport } from "../manual-reports";

export const OPEN_STATUSES: AlertStatus[] = ["NEW", "ACKNOWLEDGED", "RESPONDING", "ESCALATED"];
export const ALERT_TYPES: AlertType[] = ["WEAPON", "FIGHT_VIOLENCE", "RESTRICTED_AREA"];

/** A count, and whether it may be higher than shown (the list hit the API's page limit). */
export interface Count {
  value: number;
  atLeast: boolean;
}

export function formatCount(c: Count): string {
  return c.atLeast ? `${c.value}+` : String(c.value);
}

function isOpen(a: Alert): boolean {
  return OPEN_STATUSES.includes(a.status);
}

export interface AlertSummary {
  open: Count;
  highPriorityOpen: Count;
  unacknowledged: Count;
  openByStatus: Array<{ key: AlertStatus; value: number }>;
  openByType: Array<{ key: AlertType; value: number }>;
  /** The first open alerts in the server's order (high priority first, newest first). */
  attention: Alert[];
  /** Open counts may be incomplete: the list was cut at ``cap`` while still listing open alerts. */
  openIncomplete: boolean;
}

/** GET /alerts lists open alerts first (then high priority first, newest first), so the open
 * figures are exact unless the page limit was reached before the first closed alert. */
export function summarizeAlerts(rows: Alert[], cap: number, attention = 5): AlertSummary {
  const open = rows.filter(isOpen);
  const openIncomplete = rows.length >= cap && rows.length > 0 && isOpen(rows[rows.length - 1]);
  const count = (n: number): Count => ({ value: n, atLeast: openIncomplete });
  return {
    open: count(open.length),
    highPriorityOpen: count(open.filter((a) => a.priority === "HIGH").length),
    unacknowledged: count(open.filter((a) => a.status === "NEW").length),
    openByStatus: OPEN_STATUSES.map((key) => ({ key, value: open.filter((a) => a.status === key).length })),
    openByType: ALERT_TYPES.map((key) => ({ key, value: open.filter((a) => a.alert_type === key).length })),
    attention: open.slice(0, attention),
    openIncomplete,
  };
}

/** GET /manual-reports lists newest first; "awaiting review" is exact unless the page limit was
 * reached (older reports may also be unreviewed). */
export function summarizeReports(rows: ManualReport[], cap: number, recent = 5): { awaiting: Count; recent: ManualReport[] } {
  return {
    awaiting: { value: rows.filter((r) => r.status === "SUBMITTED").length, atLeast: rows.length >= cap },
    recent: rows.slice(0, recent),
  };
}

export interface LocationSummary {
  buildings: number;
  perimeters: number;
  floors: number;
  areas: number;
  /** Cameras / Guards linked to some location (each is linked to at most one). */
  camerasLinked: number;
  guardsAssigned: number;
}

export function summarizeLocations(tree: BuildingNode[]): LocationSummary {
  let floors = 0;
  let areas = 0;
  for (const b of tree) {
    floors += b.floors.length;
    areas += b.areas.length + b.floors.reduce((n, f) => n + f.areas.length, 0);
  }
  return {
    buildings: tree.filter((b) => b.kind === "INDOOR").length,
    perimeters: tree.filter((b) => b.kind === "OUTDOOR_PERIMETER").length,
    floors,
    areas,
    camerasLinked: tree.reduce((n, b) => n + (b.camera_count ?? 0), 0),
    guardsAssigned: tree.reduce((n, b) => n + (b.guard_count ?? 0), 0),
  };
}
