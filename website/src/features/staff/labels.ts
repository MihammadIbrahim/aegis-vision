import type { GuardCategory, StaffRole } from "./types";

export const ROLE_LABEL: Record<StaffRole, string> = { GUARD: "Guard", SUPERVISOR: "Supervisor" };

export const CATEGORY_TEXT: Record<GuardCategory, { label: string; description: string }> = {
  STANDBY: {
    label: "Standby",
    description: "No fixed post. Kept ready for campus-wide dispatch and alert escalations.",
  },
  STATIONED: {
    label: "Stationed / Standalone",
    description: "Stationed at a building, floor or area. Receives the alerts raised there automatically.",
  },
};
