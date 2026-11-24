import { Building2, ChevronDown, ChevronRight, Layers, MapPin, Pencil, Plus, RotateCw, Trash2, Trees, View } from "lucide-react";
import { useState, type ReactNode } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { canManageBuildings } from "../../../auth/permissions";
import { Button, EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { locations } from "../api";
import { AreaDialog, BuildingDialog, FloorDialog, LinkedDialog, NotEmptyDialog } from "../components/LocationDialogs";
import { LocationLinks } from "../components/LocationLinks";
import { boundaryLabel, countsLabel, plural } from "../labels";
import type { AreaNode, BuildingNode, FloorNode, LocationLevel } from "../types";

type Open =
  | { kind: "building"; building?: BuildingNode }
  | { kind: "floor"; building: BuildingNode; floor?: FloorNode }
  | { kind: "area"; building: BuildingNode; area?: AreaNode; floorId?: string | null }
  | { kind: "linked"; level: LocationLevel; id: string; name: string }
  | { kind: "not-empty"; name: string; contents: string[] };

/** What still sits in or under a location, from the tree (the server checks again on removal). */
function contentsOf(node: { camera_count: number; guard_count: number }, floors = 0, areas = 0): string[] {
  const parts: string[] = [];
  if (floors) parts.push(plural(floors, "floor"));
  if (areas) parts.push(plural(areas, "area"));
  if (node.camera_count) parts.push(plural(node.camera_count, "camera"));
  if (node.guard_count) parts.push(plural(node.guard_count, "assigned Guard"));
  return parts;
}

function allAreas(b: BuildingNode): number {
  return b.areas.length + b.floors.reduce((n, f) => n + f.areas.length, 0);
}

function Toggle({ open, label, onClick }: { open: boolean; label: string; onClick: () => void }) {
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <button type="button" className="btn btn-ghost btn-sm tree-toggle" aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${label}`} onClick={onClick}>
      <Icon className="icon" aria-hidden="true" />
    </button>
  );
}

function NodeActions({ children }: { children: ReactNode }) {
  return <div className="tree-actions">{children}</div>;
}

/** Building, Floor & Area Management for the signed-in Admin or Supervisor's own campus (D-010,
 * D-011). Buildings and perimeters are changed by Admins only; floors and areas by both roles. */
export function LocationsPage() {
  const { session } = useAuth();
  const admin = canManageBuildings(session?.role);
  const tree = useAsync(() => locations.tree(), []);
  const toast = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [open, setOpen] = useState<Open | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const toggle = (id: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  /** Runs a change, closes the dialog, reports success and reloads the tree. Errors propagate to the
   * dialog that called it. */
  async function saved(action: Promise<unknown>, message: string) {
    await action;
    setOpen(null);
    toast("success", message);
    tree.reload();
  }

  async function remove(name: string, what: string, contents: string[], run: () => Promise<void>) {
    if (contents.length) {
      setOpen({ kind: "not-empty", name, contents });
      return;
    }
    const { confirmed } = await confirm({
      title: `Remove ${what} ${name}?`,
      message: "This cannot be undone.",
      confirmLabel: `Remove ${what}`,
      tone: "danger",
    });
    if (!confirmed) return;
    try {
      await run();
      toast("success", `${name} removed.`);
      tree.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  const linkedButton = (level: LocationLevel, id: string, name: string) => (
    <Button variant="ghost" size="sm" icon={View} onClick={() => setOpen({ kind: "linked", level, id, name })} aria-label={`Cameras and Guards linked to ${name}`}>
      Linked
    </Button>
  );

  function areaItem(building: BuildingNode, area: AreaNode) {
    const boundary = boundaryLabel(area);
    return (
      <li key={area.id} className="tree-node tree-area">
        <div className="tree-row">
          <div className="tree-label">
            <MapPin className="icon" aria-hidden="true" />
            <span className="tree-name">{area.name}</span>
            {boundary && <span className="badge tone-normal">Boundary: {boundary}</span>}
          </div>
          <span className="muted small tree-counts">{countsLabel(area)}</span>
          <NodeActions>
            {linkedButton("AREA", area.id, area.name)}
            <Button variant="ghost" size="sm" icon={Pencil} aria-label={`Edit area ${area.name}`} onClick={() => setOpen({ kind: "area", building, area })}>
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={Trash2}
              aria-label={`Remove area ${area.name}`}
              onClick={() => remove(area.name, "area", contentsOf(area), () => locations.deleteArea(area.id))}
            >
              Remove
            </Button>
          </NodeActions>
        </div>
      </li>
    );
  }

  function floorItem(building: BuildingNode, floor: FloorNode) {
    const expanded = !collapsed.has(floor.id);
    return (
      <li key={floor.id} className="tree-node tree-floor">
        <div className="tree-row">
          <div className="tree-label">
            <Toggle open={expanded} label={`floor ${floor.name}`} onClick={() => toggle(floor.id)} />
            <Layers className="icon" aria-hidden="true" />
            <span className="tree-name">{floor.name}</span>
            {floor.level !== null && <span className="muted small">Level {floor.level}</span>}
          </div>
          <span className="muted small tree-counts">
            {plural(floor.areas.length, "area")} · {countsLabel(floor)}
          </span>
          <NodeActions>
            <Button variant="ghost" size="sm" icon={Plus} aria-label={`Add area to floor ${floor.name}`} onClick={() => setOpen({ kind: "area", building, floorId: floor.id })}>
              Area
            </Button>
            {linkedButton("FLOOR", floor.id, floor.name)}
            <Button variant="ghost" size="sm" icon={Pencil} aria-label={`Edit floor ${floor.name}`} onClick={() => setOpen({ kind: "floor", building, floor })}>
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={Trash2}
              aria-label={`Remove floor ${floor.name}`}
              onClick={() => remove(floor.name, "floor", contentsOf(floor, 0, floor.areas.length), () => locations.deleteFloor(floor.id))}
            >
              Remove
            </Button>
          </NodeActions>
        </div>
        {expanded && floor.areas.length > 0 && <ul className="tree-children">{floor.areas.map((a) => areaItem(building, a))}</ul>}
      </li>
    );
  }

  function buildingCard(building: BuildingNode) {
    const expanded = !collapsed.has(building.id);
    const indoor = building.kind === "INDOOR";
    const Icon = indoor ? Building2 : Trees;
    const empty = building.floors.length === 0 && building.areas.length === 0;
    return (
      <li key={building.id} className="card tree-building">
        <div className="tree-row">
          <div className="tree-label">
            <Toggle open={expanded} label={building.name} onClick={() => toggle(building.id)} />
            <Icon className="icon" aria-hidden="true" />
            <h2 className="tree-name">{building.name}</h2>
            <StatusBadge status={building.kind} />
          </div>
          <span className="muted small tree-counts">
            {indoor && `${plural(building.floors.length, "floor")} · `}
            {plural(allAreas(building), "area")} · {countsLabel(building)}
          </span>
          <NodeActions>
            {indoor && (
              <Button variant="secondary" size="sm" icon={Plus} aria-label={`Add floor to ${building.name}`} onClick={() => setOpen({ kind: "floor", building })}>
                Floor
              </Button>
            )}
            <Button variant="secondary" size="sm" icon={Plus} aria-label={`Add area to ${building.name}`} onClick={() => setOpen({ kind: "area", building, floorId: null })}>
              Area
            </Button>
            {linkedButton("BUILDING", building.id, building.name)}
            {admin && (
              <>
                <Button variant="ghost" size="sm" icon={Pencil} aria-label={`Edit ${building.name}`} onClick={() => setOpen({ kind: "building", building })}>
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  icon={Trash2}
                  aria-label={`Remove ${building.name}`}
                  onClick={() =>
                    remove(building.name, indoor ? "building" : "perimeter", contentsOf(building, building.floors.length, allAreas(building)), () =>
                      locations.deleteBuilding(building.id),
                    )
                  }
                >
                  Remove
                </Button>
              </>
            )}
          </NodeActions>
        </div>
        {expanded &&
          (empty ? (
            <p className="muted small tree-empty">{indoor ? "No floors or areas yet." : "No areas yet."}</p>
          ) : (
            <ul className="tree-children">
              {building.floors.map((f) => floorItem(building, f))}
              {building.areas.length > 0 && indoor && building.floors.length > 0 && (
                <li className="tree-node tree-group muted small">Areas not on a floor</li>
              )}
              {building.areas.map((a) => areaItem(building, a))}
            </ul>
          ))}
      </li>
    );
  }

  return (
    <section>
      <PageHeader
        title="Buildings & Locations"
        description={
          admin
            ? "Buildings, outdoor perimeters, floors and areas of this campus. Cameras and Guards are linked to these locations."
            : "Floors and areas of this campus. Buildings and outdoor perimeters are managed by Organization Admins; you can view them."
        }
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={tree.reload} loading={tree.loading && Boolean(tree.data)}>
              Refresh
            </Button>
            {admin && (
              <Button icon={Plus} onClick={() => setOpen({ kind: "building" })}>
                Add building or perimeter
              </Button>
            )}
          </>
        }
      />
      {tree.error ? (
        <ErrorState error={tree.error} onRetry={tree.reload} />
      ) : !tree.data ? (
        <LoadingState />
      ) : tree.data.length === 0 ? (
        <EmptyState
          title="No buildings or perimeters yet."
          message={admin ? "Add the first building or outdoor perimeter of this campus." : "An Organization Admin adds buildings and perimeters."}
        />
      ) : (
        <>
          <ul className="plain tree" aria-label="Campus locations">
            {tree.data.map(buildingCard)}
          </ul>
          <h2 className="dash-heading">Cameras and Guards by location</h2>
          <LocationLinks tree={tree.data} />
        </>
      )}

      {open?.kind === "building" && (
        <BuildingDialog
          building={open.building}
          onClose={() => setOpen(null)}
          onSave={(body) =>
            saved(
              open.building ? locations.updateBuilding(open.building.id, body) : locations.createBuilding(body),
              open.building ? "Changes saved." : `${body.name} added.`,
            )
          }
        />
      )}
      {open?.kind === "floor" && (
        <FloorDialog
          building={open.building}
          floor={open.floor}
          onClose={() => setOpen(null)}
          onSave={(body) =>
            saved(
              open.floor ? locations.updateFloor(open.floor.id, body) : locations.createFloor(open.building.id, body),
              open.floor ? "Changes saved." : `Floor ${body.name} added.`,
            )
          }
        />
      )}
      {open?.kind === "area" && (
        <AreaDialog
          building={open.building}
          area={open.area}
          floorId={open.floorId}
          onClose={() => setOpen(null)}
          onSave={(body) =>
            saved(
              open.area ? locations.updateArea(open.area.id, body) : locations.createArea(open.building.id, body),
              open.area ? "Changes saved." : `Area ${body.name} added.`,
            )
          }
        />
      )}
      {open?.kind === "linked" && <LinkedDialog level={open.level} id={open.id} name={open.name} onClose={() => setOpen(null)} />}
      {open?.kind === "not-empty" && <NotEmptyDialog name={open.name} contents={open.contents} onClose={() => setOpen(null)} />}
      {confirmDialog}
    </section>
  );
}
