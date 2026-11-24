// Display formatting shared by every feature.

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

/** "IT Block › Ground › Server Room", skipping missing levels; "—" when there is nothing. */
export function formatLocationPath(...parts: Array<string | null | undefined>): string {
  const present = parts.filter((p): p is string => Boolean(p));
  return present.length ? present.join(" › ") : "—";
}

/** "1.4 GB" style sizes (binary units). */
export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/** "2 h 5 min" for an uptime in seconds. */
export function formatDuration(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return `${m} min`;
}

/** "ORGANIZATION_STATUS_CHANGED" → "Organization status changed". */
export function humanize(code: string): string {
  const text = code.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}
