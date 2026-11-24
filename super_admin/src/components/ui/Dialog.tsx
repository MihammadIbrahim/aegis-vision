import { X } from "lucide-react";
import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface DialogProps {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** CSS selector of the element to focus first (default: the first focusable element). */
  initialFocus?: string;
}

/** A modal dialog on the native <dialog> element: focus moves inside, Tab stays inside, Escape
 * closes, and focus returns to what was focused before. */
export function Dialog({ open, title, onClose, children, footer, initialFocus }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const previous = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog || !open) return;
    previous.current = document.activeElement as HTMLElement | null;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    const first = (initialFocus && dialog.querySelector<HTMLElement>(initialFocus)) || dialog.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    return () => {
      if (typeof dialog.close === "function" && dialog.open) dialog.close();
      else dialog.removeAttribute("open");
      previous.current?.focus?.();
    };
  }, [open, initialFocus]);

  function onKeyDown(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !ref.current) return;
    const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const [first, last] = [items[0], items[items.length - 1]];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  if (!open) return null;
  return (
    <dialog ref={ref} className="dialog" aria-labelledby={titleId} onKeyDown={onKeyDown} onCancel={(e) => e.preventDefault()}>
      <div className="dialog-head">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="btn btn-ghost btn-sm" aria-label="Close" onClick={onClose}>
          <X className="icon" aria-hidden="true" />
        </button>
      </div>
      <div className="dialog-body">{children}</div>
      {footer && <div className="dialog-foot">{footer}</div>}
    </dialog>
  );
}
