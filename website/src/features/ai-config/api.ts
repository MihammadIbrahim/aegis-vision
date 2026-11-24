import { api } from "../../api/client";

// AI Configuration (Org Admin page 17, Supervisor page 14) and live overlays (Live items 16–22; D-024).
// D-028: per-camera opt-in (new cameras run nothing), bulk assignment, Supervisor visibility, organization blocks.

export type ModuleKey = "human_detection" | "person_reid" | "weapon_detection" | "fight_detection" | "face_recognition";
export type ModuleSet = Record<ModuleKey, boolean>;
export const MODULE_KEYS: ModuleKey[] = ["human_detection", "person_reid", "weapon_detection", "fight_detection", "face_recognition"];

export interface CameraAi {
  camera_id: string;
  camera_name: string;
  location: string | null;
  configured: ModuleSet;
  effective: ModuleSet;
  customized: boolean;
  stream_state: string;
  running: string[];
  running_now: boolean;
  frames_per_second: number | null;
  last_error: string | null;
  reported_at: string | null;
  updated_by_name: string | null;
  updated_at: string | null;
}

export interface AiConfiguration {
  modules: { key: ModuleKey; label: string }[];
  platform: ModuleSet;
  campus: ModuleSet;
  campus_permitted: ModuleSet;
  campus_customized: boolean;
  campus_updated_at: string | null;
  campus_updated_by_name: string | null;
  alert_rules: { detection: string; priority: "HIGH" | "STANDARD"; module: ModuleKey }[];
  cameras: CameraAi[];
  /** Modules the caller may switch on for a camera (platform, organization, campus and, for Supervisors,
   * visibility all allow them). */
  available_modules: ModuleKey[];
  /** Blocked for the whole organization by the Super Admin (D-028). */
  org_blocked: ModuleSet;
  blocked_by_platform: ModuleKey[];
  /** Admins only: which modules the campus's Supervisors see and manage; null for Supervisors. */
  supervisor_visible_modules: ModuleSet | null;
}

export type BulkMode = "ADD" | "REMOVE" | "SET";

export interface BulkResult {
  mode: BulkMode;
  updated_count: number;
  cameras: CameraAi[];
}

export interface Overlay {
  camera_id: string;
  captured_at: string | null;
  stale: boolean;
  frame_width: number | null;
  frame_height: number | null;
  modules: string[];
  detections: { label: string; confidence: number | null; box: number[]; track_id: number | null; module: string | null }[];
}

export const aiConfig = {
  get: () => api<AiConfiguration>("/ai-configuration"),
  saveCampus: (modules: ModuleSet, current_password: string, supervisor_visible_modules?: ModuleSet) =>
    api<AiConfiguration>("/ai-configuration/campus", {
      method: "PUT",
      json: supervisor_visible_modules ? { ...modules, supervisor_visible_modules, current_password } : { ...modules, current_password },
    }),
  bulk: (camera_ids: string[], mode: BulkMode, modules: Partial<ModuleSet>, current_password: string) =>
    api<BulkResult>("/ai-configuration/cameras/bulk", { method: "PUT", json: { camera_ids, mode, modules, current_password } }),
  saveCamera: (cameraId: string, modules: ModuleSet, current_password: string) =>
    api<AiConfiguration>(`/ai-configuration/cameras/${cameraId}`, { method: "PUT", json: { ...modules, current_password } }),
  overlay: (cameraId: string) => api<Overlay>(`/live/cameras/${cameraId}/overlay`),
};

export const SHORT_LABELS: Record<ModuleKey, string> = {
  human_detection: "People & tracking",
  person_reid: "Re-ID",
  weapon_detection: "Weapons",
  fight_detection: "Fights",
  face_recognition: "Face recognition",
};

export const NO_MODULES: ModuleSet = { human_detection: false, person_reid: false, weapon_detection: false, fight_detection: false, face_recognition: false };

/** ``set`` with only the listed modules kept on. */
export function onlyKeys(set: ModuleSet, keys: readonly ModuleKey[]): ModuleSet {
  return Object.fromEntries(MODULE_KEYS.map((k) => [k, set[k] && keys.includes(k)])) as ModuleSet;
}
