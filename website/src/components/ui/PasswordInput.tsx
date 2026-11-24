import type { LucideIcon } from "lucide-react";
import { Eye, EyeOff } from "lucide-react";
import { useState, type InputHTMLAttributes } from "react";

export interface PasswordInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  /** Optional decorative icon on the left. */
  icon?: LucideIcon;
  /** Name used in the show/hide button's label, e.g. "confirm password" (default "password"). */
  revealLabel?: string;
}

/** A password input with a show/hide button. Use it as the child of <Field>: the id and ARIA links
 * that Field adds reach the input itself. */
export function PasswordInput({ icon: Icon, revealLabel = "password", className, ...rest }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  return (
    <div className={["control has-action", Icon ? "has-icon" : "", className].filter(Boolean).join(" ")}>
      {Icon && <Icon className="control-icon" aria-hidden="true" />}
      <input {...rest} type={visible ? "text" : "password"} />
      <button
        type="button"
        className="btn btn-ghost btn-sm control-action"
        aria-label={visible ? `Hide ${revealLabel}` : `Show ${revealLabel}`}
        aria-pressed={visible}
        onClick={() => setVisible(!visible)}
      >
        {visible ? <EyeOff className="icon" aria-hidden="true" /> : <Eye className="icon" aria-hidden="true" />}
      </button>
    </div>
  );
}
