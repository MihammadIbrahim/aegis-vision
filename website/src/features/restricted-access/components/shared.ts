export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const DECISIONS: Record<string, string> = {
  AUTHORIZED: "Authorized at this time",
  NO_PERMISSION: "No access permission for this area",
  EXPIRED: "The permission has expired",
  NOT_YET_VALID: "The permission is not valid yet",
  OUTSIDE_SCHEDULE: "Outside the allowed days or hours",
  PERSONNEL_INACTIVE: "The personnel record is inactive",
  AREA_NOT_RESTRICTED: "This area is not restricted",
};

/** A datetime-local value ("2026-11-02T08:00") in the browser's time zone → ISO with offset. */
export function localToIso(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

/** ISO time → a datetime-local value in the browser's time zone (for pre-filling inputs). */
export function isoToLocal(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function areaLabel(a: { building_name: string; floor_name: string | null; area_name: string }): string {
  return [a.building_name, a.floor_name, a.area_name].filter(Boolean).join(" › ");
}
