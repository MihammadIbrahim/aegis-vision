import { api } from "../../api/client";
import type { Camera, CameraBody, CameraQuery } from "./types";

export const cameras = {
  list: (query: CameraQuery = {}) => api<Camera[]>("/cameras", { query: { ...query } }),
  get: (id: string) => api<Camera>(`/cameras/${id}`),
  create: (body: CameraBody) => api<Camera>("/cameras", { json: body }),
  update: (id: string, body: CameraBody) => api<Camera>(`/cameras/${id}`, { method: "PUT", json: body }),
  remove: (id: string) => api<void>(`/cameras/${id}`, { method: "DELETE" }),
};
