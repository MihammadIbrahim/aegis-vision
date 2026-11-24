import { Building2, Check, GitBranchPlus } from "lucide-react";
import { Link } from "react-router-dom";

/** Switch between the two ways to start: a new organization, or a campus for an existing one. */
export function RegisterSwitch({ current }: { current: "organization" | "campus" }) {
  const item = (to: string, key: "organization" | "campus", label: string, Icon: typeof Building2) => (
    <Link
      to={to}
      aria-current={current === key ? "page" : undefined}
      className={[
        "inline-flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium no-underline transition-colors",
        current === key ? "bg-surface text-fg shadow-card" : "text-fg-muted hover:text-fg",
      ].join(" ")}
    >
      <Icon className="icon" aria-hidden="true" />
      {label}
    </Link>
  );
  return (
    <nav aria-label="Registration type" className="mb-6 flex gap-1 rounded-xl border border-border bg-surface-header p-1">
      {item("/register", "organization", "New organization", Building2)}
      {item("/register/campus", "campus", "Add a campus", GitBranchPlus)}
    </nav>
  );
}

/** Three-step progress indicator: Details → Email code → the last step. */
export function Steps({ step, last }: { step: 1 | 2 | 3; last: string }) {
  return (
    <ol className="m-0 mb-5 flex list-none flex-wrap items-center gap-x-3 gap-y-2 p-0 text-sm" aria-label="Progress">
      {["Details", "Email code", last].map((name, i) => {
        const n = i + 1;
        const state = n < step ? "done" : n === step ? "current" : "next";
        return (
          <li key={name} className="flex items-center gap-2" aria-current={state === "current" ? "step" : undefined}>
            <span
              className={[
                "grid size-6 place-items-center rounded-full text-xs font-semibold",
                state === "current" ? "bg-primary text-on-primary" : "",
                state === "done" ? "bg-success-bg text-success-text" : "",
                state === "next" ? "border border-border-strong text-fg-muted" : "",
              ].join(" ")}
              aria-hidden="true"
            >
              {state === "done" ? <Check className="size-3.5" /> : n}
            </span>
            <span className={state === "current" ? "font-semibold text-fg" : "text-fg-muted"}>{name}</span>
            {n < 3 && <span className="hidden h-px w-6 bg-border sm:block" aria-hidden="true" />}
          </li>
        );
      })}
    </ol>
  );
}
