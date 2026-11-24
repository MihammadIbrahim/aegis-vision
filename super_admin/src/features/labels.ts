import type { AiModuleKey } from "../api/types";

/** Display names of the platform AI module switches (keys are the API's field names). */
export const AI_MODULE_LABELS: Record<AiModuleKey, { name: string; group: "core" | "optional" }> = {
  human_detection_tracking_enabled: { name: "Human detection & single-camera tracking", group: "core" },
  person_reid_enabled: { name: "Cross-camera person re-identification (Re-ID)", group: "core" },
  weapon_detection_enabled: { name: "Weapon detection", group: "core" },
  fight_violence_detection_enabled: { name: "Fight / violence detection", group: "core" },
  restricted_area_face_recognition_enabled: { name: "Restricted-area face recognition", group: "optional" },
  rag_assistant_enabled: { name: "RAG security assistant", group: "optional" },
  agentic_investigation_enabled: { name: "Agentic incident investigation", group: "optional" },
};
