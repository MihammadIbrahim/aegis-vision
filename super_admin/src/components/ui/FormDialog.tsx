import { useState, type FormEvent, type ReactNode } from "react";

import { Button } from "./Button";
import { Dialog } from "./Dialog";

/** A dialog holding one form. While ``onSubmit`` runs the submit button is busy; if it throws, the
 * error message is shown inside the dialog and the dialog stays open. */
export function FormDialog({
  title,
  submitLabel,
  onClose,
  onSubmit,
  disabled = false,
  children,
}: {
  title: ReactNode;
  submitLabel: string;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  disabled?: boolean;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Dialog open title={title} onClose={onClose}>
      <form className="stack" onSubmit={submit} noValidate>
        {children}
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-foot">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy} disabled={disabled}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
