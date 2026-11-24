import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { ErrorState, LoadingState } from "../../../components/ui";
import type { AsyncState } from "../../../hooks/useAsync";

/** A dashboard section: a card with a heading, an optional "view all" link, and the loading and
 * error states of the data it shows. Each section loads on its own, so one failing source does
 * not hide the rest. */
export function Panel<T>({
  id,
  title,
  link,
  state,
  className,
  children,
}: {
  id: string;
  title: string;
  link?: { to: string; label: string };
  state?: AsyncState<T>;
  className?: string;
  children: (data: T) => ReactNode;
}) {
  let body: ReactNode;
  if (state?.error) body = <ErrorState error={state.error} onRetry={state.reload} />;
  else if (state && state.data === undefined) body = <LoadingState />;
  else body = children(state?.data as T);
  return (
    <section className={["card dash-panel", className].filter(Boolean).join(" ")} aria-labelledby={id}>
      <div className="card-head">
        <h3 id={id}>{title}</h3>
        {link && (
          <Link className="dash-link" to={link.to}>
            {link.label}
            <ArrowRight className="icon" aria-hidden="true" />
          </Link>
        )}
      </div>
      {body}
    </section>
  );
}
