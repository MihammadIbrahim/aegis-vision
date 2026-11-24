import { Lock, LockOpen } from "lucide-react";

import { Button, DataTable, StatusBadge, useToast } from "../../../components/ui";
import { useConfirm } from "../../../hooks/useConfirm";
import { access } from "../api";
import type { RestrictedArea } from "../types";
import { areaLabel } from "./shared";

/** Admins choose which campus areas need permission to enter. */
export function RestrictedAreasPanel({ areas, onChange }: { areas: RestrictedArea[]; onChange: () => void }) {
  const toast = useToast();
  const { confirm, dialog } = useConfirm();

  async function run(action: () => Promise<unknown>, done: string) {
    try {
      await action();
      toast("success", done);
      onChange();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  async function clear(a: RestrictedArea) {
    const answer = await confirm({
      title: `Clear the restricted mark on ${a.area_name}?`,
      message: "Anyone may then be in this area without a permission. This is refused while people still have access.",
      confirmLabel: "Clear restriction",
      tone: "danger",
    });
    if (answer.confirmed) await run(() => access.clear(a.area_id), "Area is no longer restricted.");
  }

  return (
    <div className="card stack">
      <h2>Restricted areas</h2>
      <p className="muted small">Mark the campus areas that need permission to enter (server room, laboratory…).</p>
      <DataTable
        caption="Restricted areas"
        rows={areas}
        rowKey={(a) => a.area_id}
        empty="No areas yet. Add them in Location Management."
        columns={[
          { key: "area", header: "Area", render: (a) => areaLabel(a) },
          { key: "status", header: "Status", render: (a) => (a.is_restricted ? <StatusBadge status="RESTRICTED" /> : <span className="muted">Open</span>) },
          {
            key: "action",
            header: <span className="sr-only">Actions</span>,
            render: (a) =>
              a.is_restricted ? (
                <Button variant="ghost" size="sm" icon={LockOpen} onClick={() => void clear(a)}>
                  Clear
                </Button>
              ) : (
                <Button variant="ghost" size="sm" icon={Lock} onClick={() => void run(() => access.mark(a.area_id, null), "Area marked restricted.")}>
                  Mark restricted
                </Button>
              ),
          },
        ]}
      />
      {dialog}
    </div>
  );
}
