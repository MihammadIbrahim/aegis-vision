import { api } from "../../api/client";

// Person Search (Org Admin page 7, Supervisor page 7; requirements Section 16; decision D-024).
// Results are resemblances (LIKELY_MATCH / UNCERTAIN), never identities.

export type Band = "LIKELY_MATCH" | "UNCERTAIN";
export type Review = "PENDING" | "VERIFIED_MATCH" | "NOT_A_MATCH";

export interface Observation {
  id: string;
  camera_id: string;
  camera_name: string;
  location: string | null;
  track_id: number;
  observed_at: string;
  quality: number | null;
  has_crop: boolean;
  model_version: string;
}

export interface Candidate {
  id: string;
  search_id: string;
  rank: number;
  observation_id: string | null;
  camera_id: string | null;
  camera_name: string;
  location: string | null;
  track_id: number | null;
  observed_at: string;
  similarity: number;
  band: Band;
  has_crop: boolean;
  review: Review;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  incident_id: string | null;
  incident_reference: string | null;
  evidence_id: string | null;
}

export interface Limitation {
  camera_id: string | null;
  camera_name: string;
  issue: "CAMERA_OFFLINE" | "REID_OFF" | "NO_OBSERVATIONS";
  detail: string | null;
}

export interface PersonSearch {
  id: string;
  kind: "PHOTO" | "OBSERVATION";
  reference_observation_id: string | null;
  has_reference: boolean;
  period_from: string;
  period_to: string;
  camera_ids: string[];
  model_version: string | null;
  candidate_count: number;
  searched_observations: number;
  complete: boolean;
  limitations: Limitation[];
  note: string | null;
  incident_id: string | null;
  created_by_name: string;
  created_at: string;
  candidates: Candidate[];
}

export interface ModelStatus {
  photo_search_available: boolean;
  model_version: string | null;
  reason: string | null;
  likely_threshold: number;
  uncertain_threshold: number;
}

export interface Scope {
  since?: string;
  until?: string;
  camera_ids: string[];
  note?: string;
}

const BASE = "/person-search";

export const personSearch = {
  model: () => api<ModelStatus>(`${BASE}/model`),
  observations: (query: { camera_id?: string; since?: string; until?: string; limit?: number }) =>
    api<Observation[]>(`${BASE}/observations`, { query: { ...query } }),
  byObservation: (observation_id: string, scope: Scope) =>
    api<PersonSearch>(`${BASE}/observation`, { json: { observation_id, ...scope } }),
  byPhoto: (photo: File, scope: Scope) => {
    const form = new FormData();
    form.append("photo", photo);
    if (scope.since) form.append("since", scope.since);
    if (scope.until) form.append("until", scope.until);
    for (const id of scope.camera_ids) form.append("camera_ids", id);
    if (scope.note) form.append("note", scope.note);
    return api<PersonSearch>(`${BASE}/photo`, { form });
  },
  list: (limit = 30, offset = 0) => api<PersonSearch[]>(BASE, { query: { limit, offset } }),
  get: (id: string) => api<PersonSearch>(`${BASE}/${id}`),
  review: (candidateId: string, review: Review, note: string | null) =>
    api<Candidate>(`${BASE}/results/${candidateId}/review`, { json: { review, note } }),
  link: (candidateId: string, incident_id: string) =>
    api<Candidate>(`${BASE}/results/${candidateId}/incident`, { json: { incident_id } }),
  saveEvidence: (candidateId: string, title: string, notes: string | null, tags: string[]) =>
    api<{ evidence_id: string }>(`${BASE}/results/${candidateId}/evidence`, { json: { title, notes, tags } }),
  paths: {
    observationCrop: (id: string) => `${BASE}/observations/${id}/crop`,
    resultCrop: (id: string) => `${BASE}/results/${id}/crop`,
    resultClip: (id: string) => `${BASE}/results/${id}/clip`,
    reference: (id: string) => `${BASE}/${id}/reference`,
  },
};

export const LIMITATION_TEXT: Record<Limitation["issue"], string> = {
  CAMERA_OFFLINE: "offline now",
  REID_OFF: "Re-ID is not running",
  NO_OBSERVATIONS: "no person observed in the period",
};
