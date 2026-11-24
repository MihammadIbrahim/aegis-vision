import { MapPin, MapPinOff } from "lucide-react";
import { useState } from "react";

import { Button, DataTable, ErrorState, FormDialog, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatLocationPath } from "../../../lib/format";
import { locations } from "../api";
import type { BuildingNode, LinkedCamera, LinkedGuard, LocationPath, LocationSelection } from "../types";
import { LocationPicker, NO_LOCATION, type LocationValue } from "./LocationPicker";

type Target = { kind: "camera"; item: LinkedCamera } | { kind: "guard"; item: LinkedGuard };

function where(path: LocationPath): string {
  return formatLocationPath(path.building_name, path.floor_name, path.area_name);
}

function ChangeDialog({ target, tree, onClose, onSaved }: { target: Target; tree: BuildingNode[]; onClose: () => void; onSaved: () => void }) {
  const path = target.item.location;
  const [value, setValue] = useState<LocationValue>({ building_id: path.building_id, floor_id: path.floor_id ?? "", area_id: path.area_id ?? "" });
  const name = target.kind === "camera" ? target.item.display_name : target.item.full_name;
  return (
    <FormDialog
      title={`Change the location of ${name}`}
      submitLabel="Save location"
      disabled={!value.building_id}
      onClose={onClose}
      onSubmit={async () => {
        const body: LocationSelection = { building_id: value.building_id, floor_id: value.floor_id || null, area_id: value.area_id || null };
        if (target.kind === "camera") await locations.linkCamera(target.item.camera_id, body);
        else await locations.assignGuard(target.item.guard_id, body);
        onSaved();
      }}
    >
      <p className="dialog-message">
        {target.kind === "camera"
          ? "New alerts from this camera go to the Guards of the new location. Alerts already raised keep the location they had."
          : "The Guard receives the alerts of cameras at or inside the new location, and is signed out of the app at once."}
      </p>
      <div className="row picker-row" role="group" aria-label="New location">
        <LocationPicker tree={tree} value={value.building_id ? value : NO_LOCATION} onChange={setValue} anyBuildingLabel="Choose a building" idPrefix="link" />
      </div>
    </FormDialog>
  );
}

/** Who and what is placed where on the campus: every camera and Guard with a location (Admins and Supervisors). */
export function LocationLinks({ tree }: { tree: BuildingNode[] }) {
  const toast = useToast();
  const { confirm, dialog } = useConfirm();
  const cams = useAsync(() => locations.cameraLinks(), []);
  const guards = useAsync(() => locations.guardAssignments(), []);
  const [changing, setChanging] = useState<Target | null>(null);

  async function remove(target: Target) {
    const camera = target.kind === "camera";
    const name = camera ? target.item.display_name : target.item.full_name;
    const answer = await confirm({
      title: `Remove the location of ${name}?`,
      message: camera
        ? "The camera keeps recording, but its new alerts go to no Guard until it has a location again."
        : "The Guard becomes a Standby Guard and is signed out of the app at once.",
      confirmLabel: "Remove location",
      tone: "danger",
    });
    if (!answer.confirmed) return;
    try {
      if (camera) await locations.unlinkCamera(target.item.camera_id);
      else await locations.unassignGuard(target.item.guard_id);
      toast("success", `${name} has no location now.`);
      (camera ? cams : guards).reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  const actions = (target: Target) => (
    <div className="actions">
      <Button size="sm" variant="outline" icon={MapPin} onClick={() => setChanging(target)}>
        Change
      </Button>
      <Button size="sm" variant="ghost" icon={MapPinOff} onClick={() => void remove(target)}>
        Remove
      </Button>
    </div>
  );
  const cameraColumns: Column<LinkedCamera>[] = [
    { key: "name", header: "Camera", render: (c) => c.display_name },
    { key: "where", header: "Location", render: (c) => where(c.location) },
    { key: "actions", header: <span className="sr-only">Actions</span>, render: (c) => actions({ kind: "camera", item: c }) },
  ];
  const guardColumns: Column<LinkedGuard>[] = [
    {
      key: "name",
      header: "Guard",
      render: (g) => (
        <>
          {g.full_name} <span className="muted small mono">{g.guard_id}</span>
        </>
      ),
    },
    { key: "where", header: "Post", render: (g) => where(g.location) },
    { key: "actions", header: <span className="sr-only">Actions</span>, render: (g) => actions({ kind: "guard", item: g }) },
  ];

  return (
    <div className="grid-2">
      <section className="card" aria-labelledby="linked-cameras-title">
        <h2 id="linked-cameras-title">Cameras with a location</h2>
        {cams.error ? (
          <ErrorState error={cams.error} onRetry={cams.reload} />
        ) : (
          <DataTable caption="Cameras with a location" columns={cameraColumns} rows={cams.data ?? []} rowKey={(c) => c.camera_id} loading={!cams.data} empty="No camera has a location yet." />
        )}
      </section>
      <section className="card" aria-labelledby="linked-guards-title">
        <h2 id="linked-guards-title">Stationed Guards</h2>
        {guards.error ? (
          <ErrorState error={guards.error} onRetry={guards.reload} />
        ) : (
          <DataTable caption="Stationed Guards" columns={guardColumns} rows={guards.data ?? []} rowKey={(g) => g.guard_id} loading={!guards.data} empty="No Guard has a location; all are on Standby." />
        )}
      </section>
      {changing && (
        <ChangeDialog
          target={changing}
          tree={tree}
          onClose={() => setChanging(null)}
          onSaved={() => {
            const kind = changing.kind;
            setChanging(null);
            toast("success", "Location saved.");
            (kind === "camera" ? cams : guards).reload();
          }}
        />
      )}
      {dialog}
    </div>
  );
}
