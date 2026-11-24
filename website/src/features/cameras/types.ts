// Camera Management (GET/POST/PUT/DELETE /cameras; decision D-012). Field names are the Core API's
// (camera_dto.py). No credentials are ever sent or returned.
import type { LocationPath, LocationSelection } from "../locations";

export type ConnectionType = "RTSP" | "ONVIF";

/** CameraResponse. ``environment`` comes from the linked building or perimeter. */
export interface Camera {
  /** The Camera ID (system-generated). */
  id: string;
  campus_id: string;
  display_name: string;
  connection_type: ConnectionType;
  stream_url: string | null;
  environment: "INDOOR" | "OUTDOOR" | null;
  location: LocationPath | null;
  created_at: string | null;
  updated_at: string | null;
  /** Admins and Supervisors: the AI features selected for this camera (those visible to them; D-028). */
  ai_modules?: AiModuleKey[] | null;
  /** What it runs: platform AND NOT organization-blocked AND campus AND camera. */
  ai_effective_modules?: AiModuleKey[] | null;
}

export type AiModuleKey = "human_detection" | "person_reid" | "weapon_detection" | "fight_detection" | "face_recognition";

/** CameraRequest for POST /cameras and PUT /cameras/{id}. PUT replaces everything; ``location: null``
 * removes the camera's location. */
export interface CameraBody {
  display_name: string;
  connection_type: ConnectionType;
  stream_url: string | null;
  location: LocationSelection | null;
  /** Module key → selected. Omitted on add: no AI feature runs (D-028); omitted on edit: unchanged. */
  ai_modules?: Partial<Record<AiModuleKey, boolean>>;
}

/** GET /cameras query: ``q`` matches name, Camera ID or stream address; the location filters match
 * cameras at or below that building, floor or area. */
export interface CameraQuery {
  q?: string;
  building_id?: string;
  floor_id?: string;
  area_id?: string;
}
