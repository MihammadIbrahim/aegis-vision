// Live monitoring, recordings, retention and the evidence vault (decision D-023). Field names are the
// Core API's (recording_dto.py, storage_settings_dto.py, evidence_dto.py). No stream credentials, file
// paths or service tokens ever reach the browser.
import type { Camera } from "../cameras";

export type CameraState = "ONLINE" | "OFFLINE" | "UNKNOWN";

export interface CameraStatus {
  state: CameraState;
  last_seen_at: string | null;
  /** A short, generic reason; never the address or credentials. */
  last_error: string | null;
  source: "INGESTION" | "HEALTH_CHECK" | null;
  state_changed_at: string | null;
  reported_at: string | null;
}

export interface MonitoredCamera {
  camera: Camera;
  status: CameraStatus;
}

export interface HealthCheck {
  reachable: boolean;
  checked_at: string;
  latency_ms: number | null;
  detail: string;
  camera: MonitoredCamera;
}

export interface RecordingSegment {
  id: string;
  camera_id: string;
  camera_name: string;
  started_at: string;
  ended_at: string;
  duration_seconds: number;
  size_bytes: number;
  /** When the retention daemon may delete it (end + campus retention); set on the timeline only. */
  expires_at?: string | null;
}

export interface EventMarker {
  alert_id: string;
  camera_id: string;
  alert_type: string;
  priority: string;
  status: string;
  detected_at: string;
  last_detected_at: string;
}

export interface Timeline {
  since: string;
  until: string;
  /** The campus retention in force for routine recordings. */
  retention_seconds: number;
  cameras: MonitoredCamera[];
  /** Newest first. */
  segments: RecordingSegment[];
  /** Oldest first. */
  markers: EventMarker[];
}

export interface TimelineQuery {
  camera_id?: string;
  q?: string;
  building_id?: string;
  floor_id?: string;
  area_id?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export type RetentionUnit = "MINUTES" | "HOURS" | "DAYS";

export interface Retention {
  value: number;
  unit: RetentionUnit;
  seconds: number;
  is_default: boolean;
  updated_at: string | null;
  updated_by: string | null;
  updated_by_name: string | null;
}

export interface StorageStatus {
  node: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  percent_used: number;
  level: "OK" | "WARNING" | "CRITICAL";
  reported_at: string;
}

/** What this campus stores in each tier (from the indexes; the disk is shared by the storage node). */
export interface CampusUsage {
  recording_segments: number;
  recording_bytes: number;
  oldest_recording_at: string | null;
  evidence_items: number;
  evidence_bytes: number;
}

export interface StorageOverview {
  retention: Retention;
  default_retention: Retention;
  /** Null until the retention daemon has reported. */
  storage: StorageStatus | null;
  usage: CampusUsage;
  /** "LOCAL_DISK": media under CCTV_STORAGE_ROOT on the storage node (no cloud object store yet). */
  provider: string;
  warning_percent: number;
  critical_percent: number;
}

export type EvidenceKind = "CLIP" | "SNAPSHOT";

export interface EvidenceItem {
  id: string;
  kind: EvidenceKind;
  title: string;
  notes: string;
  investigation_ref: string | null;
  source: "ALERT" | "PLAYBACK" | "PERSON_SEARCH";
  alert_id: string | null;
  camera_id: string | null;
  camera_name: string;
  covers_from: string;
  covers_to: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  saved_by: string | null;
  saved_by_name: string;
  saved_at: string;
  updated_at: string;
  // Decision D-024: labels, the linked case and the camera's location when saved.
  tags?: string[];
  incident_id?: string | null;
  incident_reference?: string | null;
  building_id?: string | null;
  floor_id?: string | null;
  area_id?: string | null;
  location?: string | null;
}

export interface EvidenceDetails {
  title: string;
  notes: string | null;
  investigation_ref: string | null;
  tags?: string[];
  /** Saving only: link the new item to this open case. */
  incident_id?: string | null;
}

export interface EvidenceQuery {
  q?: string;
  kind?: EvidenceKind;
  camera_id?: string;
  alert_id?: string;
  /** Items whose covered period overlaps [since, until] (ISO 8601). */
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
  // Decision D-024.
  tag?: string;
  building_id?: string;
  floor_id?: string;
  area_id?: string;
  incident_id?: string;
}
