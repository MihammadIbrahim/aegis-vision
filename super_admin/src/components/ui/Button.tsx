import type { LucideIcon } from "lucide-react";
import { LoaderCircle } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "md" | "sm";
  icon?: LucideIcon;
  /** Shows a spinner, disables the button and tells assistive technology it is busy. */
  loading?: boolean;
  children?: ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  icon: Icon,
  loading = false,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  const classes = ["btn", `btn-${variant}`, `btn-${size}`, className].filter(Boolean).join(" ");
  return (
    <button type={type} className={classes} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <LoaderCircle className="icon spin" aria-hidden="true" /> : Icon && <Icon className="icon" aria-hidden="true" />}
      {children}
    </button>
  );
}
