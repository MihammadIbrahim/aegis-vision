import type { LucideIcon } from "lucide-react";
import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

interface ControlProps {
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  required?: boolean;
}

export interface FieldProps {
  label: ReactNode;
  /** Shows the required marker; the control itself is marked required too. */
  required?: boolean;
  hint?: ReactNode;
  error?: ReactNode;
  className?: string;
  /** Optional icon shown inside the control, on the left (decorative). */
  icon?: LucideIcon;
  /** One input, select or textarea. It receives the id and ARIA links. */
  children: ReactElement<ControlProps>;
}

/** Label + required/optional marker + control + hint + error, linked for screen readers. */
export function Field({ label, required = false, hint, error, className, icon: Icon, children }: FieldProps) {
  const base = useId();
  const id = children.props.id ?? `${base}-control`;
  const hintId = hint ? `${base}-hint` : undefined;
  const errorId = error ? `${base}-error` : undefined;
  const describedBy = [children.props["aria-describedby"], hintId, errorId].filter(Boolean).join(" ") || undefined;
  const control = isValidElement(children)
    ? cloneElement(children, {
        id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
        required: required || children.props.required,
      })
    : children;
  return (
    <div className={["field", className].filter(Boolean).join(" ")}>
      <label htmlFor={id}>
        {label}{" "}
        {required ? (
          <span className="req" aria-hidden="true" title="Required">
            *
          </span>
        ) : (
          <span className="opt">(optional)</span>
        )}
      </label>
      {Icon ? (
        <div className="control has-icon">
          <Icon className="control-icon" aria-hidden="true" />
          {control}
        </div>
      ) : (
        control
      )}
      {hint && (
        <span id={hintId} className="hint">
          {hint}
        </span>
      )}
      {error && (
        <span id={errorId} className="field-error">
          {error}
        </span>
      )}
    </div>
  );
}
