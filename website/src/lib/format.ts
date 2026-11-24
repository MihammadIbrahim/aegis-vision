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
