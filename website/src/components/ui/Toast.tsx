import { CircleCheck, CircleX, Info, TriangleAlert, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

export type ToastKind = "success" | "warning" | "error" | "info";
interface ToastItem {
  id: number;
  kind: ToastKind;
  message: ReactNode;
}

const ICONS = { success: CircleCheck, warning: TriangleAlert, error: CircleX, info: Info };
const AUTO_DISMISS_MS = 6000;

const ToastContext = createContext<((kind: ToastKind, message: ReactNode) => void) | null>(null);

/** Short confirmations of completed actions. Errors stay until dismissed; others close after 6 s. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setItems((all) => all.filter((t) => t.id !== id)), []);
  const show = useCallback(
    (kind: ToastKind, message: ReactNode) => {
      const id = next.current++;
      setItems((all) => [...all, { id, kind, message }]);
      if (kind !== "error") window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss],
  );
  const value = useMemo(() => show, [show]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items
          .filter((t) => t.kind !== "error")
          .map((t) => (
            <ToastView key={t.id} item={t} onDismiss={dismiss} />
          ))}
      </div>
      <div className="toasts toasts-errors" role="alert" aria-live="assertive">
        {items
          .filter((t) => t.kind === "error")
          .map((t) => (
            <ToastView key={t.id} item={t} onDismiss={dismiss} />
          ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastView({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  const Icon = ICONS[item.kind];
  return (
    <div className={`toast notice ${item.kind}`}>
      <Icon className="icon" aria-hidden="true" />
      <span>{item.message}</span>
      <button type="button" className="btn btn-ghost btn-sm" aria-label="Dismiss" onClick={() => onDismiss(item.id)}>
        <X className="icon" aria-hidden="true" />
      </button>
    </div>
  );
}

export function useToast(): (kind: ToastKind, message: ReactNode) => void {
  const show = useContext(ToastContext);
  if (!show) throw new Error("useToast must be used inside ToastProvider");
  return show;
}
