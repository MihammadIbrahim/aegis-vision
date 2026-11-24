import { useState } from "react";

import { Button, DataTable, Dialog, ErrorState, Field, FormDialog, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatLocationPath } from "../../../lib/format";
import { locations } from "../api";
import { DIRECTION_OPTIONS, KIND_OPTIONS, SIDE_OPTIONS } from "../labels";
import type {
  AreaBody,
  AreaNode,
  BoundaryDirection,
  BoundarySide,
  BuildingBody,
  BuildingKind,
  BuildingNode,
  FloorBody,
  FloorNode,
  LinkedCamera,
  LinkedGuard,
  LocationLevel,
  LocationPath,
} from "../types";

/** The server accepts 1–120 characters after trimming (domain rule, location.py). */
export const MAX_NAME = 120;
export const MIN_LEVEL = -20;
export const MAX_LEVEL = 300;

function nameError(name: string, touched: boolean): string | undefined {
  if (!touched) return undefined;
  return name.trim() ? undefined : "Enter a name.";
}

// --- Building / perimeter (Admins only) -------------------------------------------------------

export function BuildingDialog({
  building,
  onClose,
  onSave,
}: {
  building?: BuildingNode;
  onClose: () => void;
  onSave: (body: BuildingBody) => Promise<void>;
}) {
  const [name, setName] = useState(building?.name ?? "");
  const [kind, setKind] = useState<BuildingKind>(building?.kind ?? "INDOOR");
  const [touched, setTouched] = useState(false);
  const hasFloors = Boolean(building && building.floors.length > 0);
  return (
    <FormDialog
      title={building ? `Edit ${building.name}` : "Add building or perimeter"}
      submitLabel={building ? "Save changes" : "Add"}
      onClose={onClose}
      disabled={!name.trim()}
      onSubmit={() => onSave({ name: name.trim(), kind })}
    >
      <Field label="Name" required error={nameError(name, touched)}>
        <input value={name} maxLength={MAX_NAME} onChange={(e) => setName(e.target.value)} onBlur={() => setTouched(true)} />
      </Field>
      <Field
        label="Type"
        required
        hint={hasFloors && kind === "INDOOR" ? "Remove this building's floors before making it an outdoor perimeter." : "Outdoor perimeters have no floors."}
      >
        <select value={kind} onChange={(e) => setKind(e.target.value as BuildingKind)}>
          {KIND_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>
    </FormDialog>
  );
}

// --- Floor (indoor buildings only) ---------------------------------------------------------------

export function FloorDialog({
  building,
  floor,
  onClose,
  onSave,
}: {
  building: BuildingNode;
  floor?: FloorNode;
  onClose: () => void;
  onSave: (body: FloorBody) => Promise<void>;
}) {
  const [name, setName] = useState(floor?.name ?? "");
  const [level, setLevel] = useState(floor?.level === null || floor?.level === undefined ? "" : String(floor.level));
  const [touched, setTouched] = useState(false);
  const parsed = level.trim() === "" ? null : Number(level);
  const levelError =
    parsed !== null && (!Number.isInteger(parsed) || parsed < MIN_LEVEL || parsed > MAX_LEVEL)
      ? `Enter a whole number from ${MIN_LEVEL} to ${MAX_LEVEL}, or leave it empty.`
      : undefined;
  return (
    <FormDialog
      title={floor ? `Edit floor ${floor.name}` : `Add floor to ${building.name}`}
      submitLabel={floor ? "Save changes" : "Add floor"}
      onClose={onClose}
      disabled={!name.trim() || Boolean(levelError)}
      onSubmit={() => onSave({ name: name.trim(), level: parsed })}
    >
      <Field label="Floor name" required error={nameError(name, touched)}>
        <input value={name} maxLength={MAX_NAME} onChange={(e) => setName(e.target.value)} onBlur={() => setTouched(true)} />
      </Field>
      <Field label="Level" hint="A number for ordering, e.g. -1 for a basement, 0 for the ground floor." error={levelError}>
        <input type="number" inputMode="numeric" step={1} min={MIN_LEVEL} max={MAX_LEVEL} value={level} onChange={(e) => setLevel(e.target.value)} />
      </Field>
    </FormDialog>
  );
}

// --- Area / zone with its optional boundary --------------------------------------------------------

export function AreaDialog({
  building,
  area,
  floorId,
  onClose,
  onSave,
}: {
  building: BuildingNode;
  area?: AreaNode;
  /** Pre-selected floor when adding an area from a floor. */
  floorId?: string | null;
  onClose: () => void;
  onSave: (body: AreaBody) => Promise<void>;
}) {
  const [name, setName] = useState(area?.name ?? "");
  const [floor, setFloor] = useState(area?.floor_id ?? floorId ?? "");
  const [direction, setDirection] = useState<BoundaryDirection | "">(area?.boundary_direction ?? "");
  const [customName, setCustomName] = useState(area?.boundary_name ?? "");
  const [side, setSide] = useState<BoundarySide | "">(area?.boundary_side ?? "");
  const [touched, setTouched] = useState(false);
  const custom = direction === "CUSTOM";
  const customMissing = custom && !customName.trim();
  return (
    <FormDialog
      title={area ? `Edit area ${area.name}` : `Add area to ${building.name}`}
      submitLabel={area ? "Save changes" : "Add area"}
      onClose={onClose}
      disabled={!name.trim() || customMissing}
      onSubmit={() =>
        onSave({
          name: name.trim(),
          floor_id: floor || null,
          boundary_direction: direction || null,
          boundary_name: custom ? customName.trim() : null,
          boundary_side: side || null,
        })
      }
    >
      <Field label="Area / zone name" required error={nameError(name, touched)}>
        <input value={name} maxLength={MAX_NAME} onChange={(e) => setName(e.target.value)} onBlur={() => setTouched(true)} />
      </Field>
      {building.kind === "INDOOR" && building.floors.length > 0 && (
        <Field label="Floor">
          <select value={floor} onChange={(e) => setFloor(e.target.value)}>
            <option value="">Not on a floor (whole building)</option>
            {building.floors.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      <fieldset className="form-grid">
        <legend>Boundary</legend>
        <Field label="Direction" hint="North, South, East, West, or a custom name.">
          <select value={direction} onChange={(e) => setDirection(e.target.value as BoundaryDirection | "")}>
            <option value="">No direction</option>
            {DIRECTION_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        {custom && (
          <Field label="Boundary name" required error={customMissing && touched ? "A custom boundary needs a name." : undefined}>
            <input value={customName} maxLength={MAX_NAME} onChange={(e) => setCustomName(e.target.value)} onBlur={() => setTouched(true)} />
          </Field>
        )}
        <Field label="Inside / outside">
          <select value={side} onChange={(e) => setSide(e.target.value as BoundarySide | "")}>
            <option value="">Not set</option>
            {SIDE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      </fieldset>
    </FormDialog>
  );
}

// --- What is linked to a location (cameras and Guards at or below it) -----------------------------

function pathText(p: LocationPath): string {
  return formatLocationPath(p.building_name, p.floor_name, p.area_name);
}

const CAMERA_COLUMNS: Column<LinkedCamera>[] = [
  { key: "name", header: "Camera", render: (c) => c.display_name },
  { key: "location", header: "Linked at", render: (c) => pathText(c.location) },
];

const GUARD_COLUMNS: Column<LinkedGuard>[] = [
  { key: "id", header: "Guard ID", render: (g) => <span className="mono">{g.guard_id}</span> },
  { key: "name", header: "Name", render: (g) => g.full_name },
  { key: "location", header: "Assigned at", render: (g) => pathText(g.location) },
];

export function LinkedDialog({ level, id, name, onClose }: { level: LocationLevel; id: string; name: string; onClose: () => void }) {
  const linked = useAsync(() => locations.linked(level, id), [level, id]);
  return (
    <Dialog
      open
      title={`Linked to ${name}`}
      onClose={onClose}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      {linked.error ? (
        <ErrorState error={linked.error} onRetry={linked.reload} />
      ) : (
        <div className="stack">
          <h3 className="dash-subhead">Cameras</h3>
          <DataTable
            caption={`Cameras linked to ${name}`}
            columns={CAMERA_COLUMNS}
            rows={linked.data?.cameras ?? []}
            rowKey={(c) => c.camera_id}
            loading={!linked.data}
            empty="No cameras here."
          />
          <h3 className="dash-subhead">Guards</h3>
          <DataTable
            caption={`Guards assigned to ${name}`}
            columns={GUARD_COLUMNS}
            rows={linked.data?.guards ?? []}
            rowKey={(g) => g.guard_id}
            loading={!linked.data}
            empty="No Guards assigned here."
          />
        </div>
      )}
    </Dialog>
  );
}

/** Shown instead of a removal confirmation when the tree says the location is still in use. */
export function NotEmptyDialog({ name, contents, onClose }: { name: string; contents: string[]; onClose: () => void }) {
  return (
    <Dialog
      open
      title={`${name} cannot be removed yet`}
      onClose={onClose}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      <p className="dialog-message" role="alert">
        It still has {contents.join(", ")}. Move or remove them first.
      </p>
    </Dialog>
  );
}
