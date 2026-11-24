import { statusLook } from "../../lib/status";

/** A status as icon + text label in its tone. ``label`` overrides the default wording. */
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const look = statusLook(status);
  const Icon = look.icon;
  return (
    <span className={`badge tone-${look.tone}`}>
      <Icon className="icon" aria-hidden="true" />
      {label ?? look.label}
    </span>
  );
}
