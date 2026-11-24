import { api } from "../../api/client";
import type {
  Area,
  AreaBody,
  Building,
  BuildingBody,
  BuildingNode,
  Floor,
  FloorBody,
  LinkedCamera,
  LinkedGuard,
  LinkedToLocation,
  LocationLevel,
  LocationSelection,
} from "./types";

const LEVEL_PATH: Record<LocationLevel, "buildings" | "floors" | "areas"> = {
  BUILDING: "buildings",
  FLOOR: "floors",
  AREA: "areas",
};

// Buildings are Admin-only on the server; floors and areas are for Admins and Supervisors.
export const locations = {
  tree: () => api<BuildingNode[]>("/locations"),
  createBuilding: (body: BuildingBody) => api<Building>("/locations/buildings", { json: body }),
  updateBuilding: (id: string, body: BuildingBody) => api<Building>(`/locations/buildings/${id}`, { method: "PUT", json: body }),
  deleteBuilding: (id: string) => api<void>(`/locations/buildings/${id}`, { method: "DELETE" }),
  createFloor: (buildingId: string, body: FloorBody) => api<Floor>(`/locations/buildings/${buildingId}/floors`, { json: body }),
  updateFloor: (id: string, body: FloorBody) => api<Floor>(`/locations/floors/${id}`, { method: "PUT", json: body }),
  deleteFloor: (id: string) => api<void>(`/locations/floors/${id}`, { method: "DELETE" }),
  createArea: (buildingId: string, body: AreaBody) => api<Area>(`/locations/buildings/${buildingId}/areas`, { json: body }),
  updateArea: (id: string, body: AreaBody) => api<Area>(`/locations/areas/${id}`, { method: "PUT", json: body }),
  deleteArea: (id: string) => api<void>(`/locations/areas/${id}`, { method: "DELETE" }),
  linked: (level: LocationLevel, id: string) => api<LinkedToLocation>(`/locations/${LEVEL_PATH[level]}/${id}/linked`),
  /** Cameras and Guards that have a location, campus-wide (Admins and Supervisors). */
  cameraLinks: () => api<LinkedCamera[]>("/locations/camera-links"),
  linkCamera: (cameraId: string, body: LocationSelection) => api<LinkedCamera>(`/locations/camera-links/${cameraId}`, { method: "PUT", json: body }),
  unlinkCamera: (cameraId: string) => api<void>(`/locations/camera-links/${cameraId}`, { method: "DELETE" }),
  guardAssignments: () => api<LinkedGuard[]>("/locations/guard-assignments"),
  assignGuard: (guardId: string, body: LocationSelection) =>
    api<LinkedGuard>(`/locations/guard-assignments/${encodeURIComponent(guardId)}`, { method: "PUT", json: body }),
  unassignGuard: (guardId: string) => api<void>(`/locations/guard-assignments/${encodeURIComponent(guardId)}`, { method: "DELETE" }),
};
