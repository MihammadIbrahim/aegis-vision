import { api, buildUrl, download } from "../../api/client";

// Reports & Analytics and the dashboard feed (Org Admin pages 1 and 22, Supervisor pages 1 and 18; D-024).

export type Preset = "TODAY" | "LAST_7_DAYS" | "LAST_30_DAYS" | "CUSTOM";

export interface ReportQuery {
  period: Preset;
  since?: string;
  until?: string;
  building_id?: string;
  floor_id?: string;
  area_id?: string;
}

export interface Counters {
  cameras_total: number;
  cameras_online: number;
  cameras_offline: number;
  guards_total: number;
  guards_active: number;
  guards_stationed: number;
  supervisors_total: number;
  supervisors_active: number;
  alerts_total: number;
  alerts_high: number;
  alerts_pending: number;
  alerts_open: number;
  incidents_active: number;
  incidents_resolved: number;
  incidents_opened: number;
  manual_reports: number;
}

export interface DayRow {
  day: string;
  high: number;
  standard: number;
  incidents: number;
  manual_reports: number;
}

export interface Report {
  period: { preset: Preset; since: string; until: string; time_zone: string };
  campus: string;
  overview: Counters & { guard_response_rate_percent: number | null };
  alerts_by_type: { alert_type: string; priority: string; count: number }[];
  alerts_by_status: { status: string; count: number }[];
  daily: DayRow[];
  response_times: {
    alerts: number;
    acknowledged: number;
    responded: number;
    resolved: number;
    acknowledge_avg_seconds: number | null;
    acknowledge_median_seconds: number | null;
    respond_avg_seconds: number | null;
    respond_median_seconds: number | null;
    resolve_avg_seconds: number | null;
    resolve_median_seconds: number | null;
    incidents_resolved: number;
    incident_resolve_avg_seconds: number | null;
    incident_resolve_median_seconds: number | null;
  };
  guard_activity: {
    guard_id: string;
    full_name: string;
    status: string;
    received: number;
    acknowledged: number;
    responding: number;
    under_control: number;
    additional_required: number;
    manual_reports: number;
  }[];
  manual_reports: {
    by_type: { report_type: string; status: string; count: number; linked_to_cases: number }[];
    total: number;
    reviewed: number;
  };
  locations: { building: string | null; floor: string | null; area: string | null; alerts: number; incidents: number; manual_reports: number }[];
  camera_uptime: {
    cameras: { camera_id: string; camera_name: string; state: string; uptime_percent: number | null; known_hours: number; failures: number }[];
    overall_uptime_percent: number | null;
    failures_by_day: { day: string; failures: number }[];
  };
  storage: {
    daily: { day: string; bytes: number; segments: number }[];
    recordings_bytes: number;
    recordings_segments: number;
    oldest_recording: string | null;
    evidence_bytes: number;
    evidence_items: number;
    average_bytes_per_day: number;
    projected_days_until_full: number | null;
    nodes: { node: string; total_bytes: number; used_bytes: number; free_bytes: number; percent_used: number; level: string; reported_at: string }[];
  };
  person_observations: number;
}

export interface DashboardFeed {
  campus: string;
  counters: Counters;
  urgent_alerts: { id: string; alert_type: string; camera_name: string; status: string; created_at: string }[];
  recent_alerts: { id: string; alert_type: string; priority: string; status: string; camera_name: string; location: string | null; created_at: string; confidence: number | null }[];
  recent_incidents: { id: string; reference: string; title: string; status: string; priority: string; assigned_name: string | null; updated_at: string }[];
  guards: { guard_id: string; full_name: string; stationed: boolean; location: string | null }[];
  alerts_by_type: { alert_type: string; priority: string; count: number }[];
  daily: DayRow[];
  person_observations: number;
  activity: { occurred_at: string; action: string; category: string; actor_name: string | null; actor_role: string; target_label: string | null; outcome: string }[];
}

export const analytics = {
  report: (query: ReportQuery) => api<Report>("/analytics", { query: { ...query } }),
  dashboard: () => api<DashboardFeed>("/analytics/dashboard"),
  export: (query: ReportQuery, format: "csv" | "pdf") =>
    download(buildUrl("/analytics/export", { ...query, format }).replace(/^\/api\/v1/, ""), `security-report.${format}`),
};

export function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}
