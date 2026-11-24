import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function PageHeader({
  title,
  description,
  actions,
  back,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  back?: { to: string; label: string };
}) {
  return (
    <header className="page-header">
      <div>
        {back && (
          <Link className="back-link" to={back.to}>
            <ChevronLeft className="icon" aria-hidden="true" />
            {back.label}
          </Link>
        )}
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}
