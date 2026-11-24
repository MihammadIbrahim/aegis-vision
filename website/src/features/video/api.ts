import { api, apiBlob } from "../../api/client";
import type { CameraQuery } from "../cameras";
import type {
  EvidenceDetails,
  EvidenceItem,
  EvidenceKind,
  EvidenceQuery,
  HealthCheck,
  MonitoredCamera,
  RetentionUnit,
  StorageOverview,
  Timeline,
  TimelineQuery,
} from "./types";

/** Live monitoring (recording_router.py). Frames are fetched with the session token and shown through
 * object URLs, so no camera address or token ever appears in an <img src>. */
export const live = {
  cameras: (query: CameraQuery = {}) => api<MonitoredCamera[]>("/live/cameras", { query: { ...query } }),
  /** Audits the start of a live view; an empty list means every camera of the campus. */
  startSession: (cameraIds: string[]) => api<MonitoredCamera[]>("/live/sessions", { json: { camera_ids: cameraIds } }),
  frame: (cameraId: string) => apiBlob(`/live/cameras/${cameraId}/frame`),
  healthCheck: (cameraId: string) => api<HealthCheck>(`/live/cameras/${cameraId}/health-check`, { method: "POST" }),
};

export const recordings = {
  timeline: (query: TimelineQuery) => api<Timeline>("/recordings", { query: { ...query } }),
  /** The MP4 of one segment (each request is audited as a playback). */
  media: (segmentId: string) => apiBlob(`/recordings/${segmentId}/media`),
  retention: () => api<StorageOverview>("/recordings/retention"),
  setRetention: (value: number, unit: RetentionUnit, currentPassword: string) =>
    api<StorageOverview>("/recordings/retention", { method: "PUT", json: { value, unit, current_password: currentPassword } }),
};

export const evidence = {
  list: (query: EvidenceQuery = {}) => api<EvidenceItem[]>("/evidence", { query: { ...query } }),
  get: (id: string) => api<EvidenceItem>(`/evidence/${id}`),
  /** The clip or snapshot (each request is audited as a view). */
  media: (id: string) => apiBlob(`/evidence/${id}/media`),
  fromAlert: (alertId: string, kind: EvidenceKind, details: EvidenceDetails) =>
    api<EvidenceItem>("/evidence/from-alert", { json: { alert_id: alertId, kind, ...details } }),
  fromPlayback: (segmentId: string, kind: EvidenceKind, startOffset: number, endOffset: number | null, details: EvidenceDetails) =>
    api<EvidenceItem>("/evidence/from-playback", {
      json: { segment_id: segmentId, kind, start_offset_seconds: startOffset, end_offset_seconds: endOffset, ...details },
    }),
  update: (id: string, details: EvidenceDetails) => api<EvidenceItem>(`/evidence/${id}`, { method: "PATCH", json: details }),
  /** Admins only; confirmed with the Admin's own password. */
  remove: (id: string, currentPassword: string) =>
    api<void>(`/evidence/${id}/delete`, { method: "POST", json: { current_password: currentPassword } }),
};
