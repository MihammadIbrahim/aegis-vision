import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

/** One headline number with its label. With ``to`` the whole tile opens the related page. */
export function StatTile({
  label,
  value,
  hint,
  icon: Icon,
  to,
  emphasis = false,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: LucideIcon;
  to?: string;
  /** Marks a figure that needs attention (e.g. open high-priority alerts above zero). */
  emphasis?: boolean;
}) {
  const content = (
    <>
      <span className="stat-label">
        {Icon && (
          <span className="stat-icon" aria-hidden="true">
            <Icon className="icon" />
          </span>
        )}
        {label}
      </span>
      <span className="stat-value">{value}</span>
      {hint && <span className="stat-hint">{hint}</span>}
    </>
  );
  const className = `stat-tile${emphasis ? " is-emphasis" : ""}`;
  return to ? (
    <Link className={className} to={to}>
      {content}
    </Link>
  ) : (
    <div className={className}>{content}</div>
  );
}

export function StatGrid({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="stat-grid" role="group" aria-label={label}>
      {children}
    </div>
  );
}
