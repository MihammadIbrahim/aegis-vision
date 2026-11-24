import type { AreaNode, BoundaryDirection, BoundarySide, BuildingKind } from "./types";

export const KIND_OPTIONS: Array<[BuildingKind, string]> = [
  ["INDOOR", "Building (indoor)"],
  ["OUTDOOR_PERIMETER", "Outdoor perimeter"],
];

export const DIRECTION_OPTIONS: Array<[BoundaryDirection, string]> = [
  ["NORTH", "North"],
  ["SOUTH", "South"],
  ["EAST", "East"],
  ["WEST", "West"],
  ["CUSTOM", "Custom name"],
];

export const SIDE_OPTIONS: Array<[BoundarySide, string]> = [
  ["INSIDE", "Inside"],
  ["OUTSIDE", "Outside"],
];

const DIRECTION = Object.fromEntries(DIRECTION_OPTIONS) as Record<BoundaryDirection, string>;
const SIDE = Object.fromEntries(SIDE_OPTIONS) as Record<BoundarySide, string>;

/** "North · Inside", "Main gate · Outside", "Inside"; null when the area has no boundary. */
export function boundaryLabel(area: Pick<AreaNode, "boundary_direction" | "boundary_name" | "boundary_side">): string | null {
  const direction = area.boundary_direction === "CUSTOM" ? area.boundary_name : area.boundary_direction ? DIRECTION[area.boundary_direction] : null;
  const parts = [direction, area.boundary_side ? SIDE[area.boundary_side] : null].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "2 cameras · 1 Guard". */
export function countsLabel(node: { camera_count: number; guard_count: number }): string {
  return `${plural(node.camera_count, "camera")} · ${plural(node.guard_count, "Guard")}`;
}
