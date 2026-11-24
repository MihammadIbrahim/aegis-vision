import { useState, type FormEvent, type ReactNode } from "react";

import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { Field } from "./Field";

export interface ConfirmOptions {
  title: string;
  message?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** "danger" for actions that remove access or deactivate records. */
  tone?: "primary" | "danger";
  /** Ask for a reason (sent to the server as the request's reason). */
  reason?: { label: string; required?: boolean; maxLength?: number };
}

export interface ConfirmResult {
  confirmed: boolean;
  reason: string | null;
}

export function ConfirmDialog({
  options,
  onResult,
}: {
  options: ConfirmOptions;
  onResult: (result: ConfirmResult) => void;
}) {
  const [reason, setReason] = useState("");
  const needsReason = Boolean(options.reason?.required);
  const cancel = () => onResult({ confirmed: false, reason: null });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (needsReason && !reason.trim()) return;
    onResult({ confirmed: true, reason: reason.trim() || null });
  }

  return (
    <Dialog open title={options.title} onClose={cancel} initialFocus={options.reason ? "textarea" : undefined}>
      <form onSubmit={submit} className="stack">
        {options.message && <div className="dialog-message">{options.message}</div>}
        {options.reason && (
          <Field label={options.reason.label} required={needsReason}>
            <textarea value={reason} maxLength={options.reason.maxLength ?? 500} onChange={(e) => setReason(e.target.value)} />
          </Field>
        )}
        <div className="dialog-foot">
          <Button variant="secondary" onClick={cancel}>
            {options.cancelLabel ?? "Cancel"}
          </Button>
          <Button type="submit" variant={options.tone === "danger" ? "danger" : "primary"} disabled={needsReason && !reason.trim()}>
            {options.confirmLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
