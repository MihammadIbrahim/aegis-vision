import { Inbox, LoaderCircle, RotateCw, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "./Button";

export function EmptyState({ title, message, action }: { title: string; message?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state">
      <Inbox className="icon state-icon" aria-hidden="true" />
      <p className="state-title">{title}</p>
      {message && <p className="muted">{message}</p>}
      {action}
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <LoaderCircle className="icon state-icon spin" aria-hidden="true" />
      <p className="muted">{label}</p>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="state notice error" role="alert">
      <TriangleAlert className="icon" aria-hidden="true" />
      <p>{message}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" icon={RotateCw} onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
