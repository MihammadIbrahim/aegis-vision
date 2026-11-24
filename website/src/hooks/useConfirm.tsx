import { useCallback, useRef, useState, type ReactNode } from "react";

import { ConfirmDialog, type ConfirmOptions, type ConfirmResult } from "../components/ui/ConfirmDialog";

/** ``const { confirm, dialog } = useConfirm();`` then render ``{dialog}`` and
 * ``const { confirmed, reason } = await confirm({...})``. Replaces window.prompt / window.confirm. */
export function useConfirm(): { confirm: (options: ConfirmOptions) => Promise<ConfirmResult>; dialog: ReactNode } {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((result: ConfirmResult) => void) | null>(null);

  const confirm = useCallback((next: ConfirmOptions) => {
    resolver.current?.({ confirmed: false, reason: null });
    setOptions(next);
    return new Promise<ConfirmResult>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  const dialog = options ? (
    <ConfirmDialog
      key={options.title}
      options={options}
      onResult={(result) => {
        resolver.current?.(result);
        resolver.current = null;
        setOptions(null);
      }}
    />
  ) : null;
  return { confirm, dialog };
}
