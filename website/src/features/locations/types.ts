// Building, Floor & Area Management (GET/POST/PUT/DELETE /locations…; decisions D-010, D-011). Field
// names are the Core API's (location_dto.py).

export type BuildingKind = "INDOOR" | "OUTDOOR_PERIMETER";
export type BoundaryDirection = "NORTH" | "SOUTH" | "EAST" | "WEST" | "CUSTOM";
export type BoundarySide = "INSIDE" | "OUTSIDE";
export type LocationLevel = "BUILDING" | "FLOOR" | "AREA";

/** An area in the tree (AreaTreeResponse). Counts are cameras/Guards linked to the area. */
export interface AreaNode {
  id: string;
  building_id: string;
  floor_id: string | null;
  name: string;
  boundary_direction: BoundaryDirection | null;
  boundary_name: string | null;
  boundary_side: BoundarySide | null;
  camera_count: number;
  guard_count: number;
}

/** A floor in the tree (FloorTreeResponse). Counts are at or below the floor. */
export interface FloorNode {
  id: string;
  building_id: string;
  name: string;
  level: number | null;
  camera_count: number;
  guard_count: number;
  areas: AreaNode[];
}

/** A building or outdoor perimeter in the tree (BuildingTreeResponse). */
export interface BuildingNode {
  id: string;
  name: string;
  kind: BuildingKind;
  /** Cameras linked at or below this building. */
  camera_count: number;
  /** Guards assigned at or below this building (active or not). */
  guard_count: number;
  floors: FloorNode[];
  /** Areas not on a floor (all areas of a perimeter). */
  areas: AreaNode[];
}

/** POST /locations/buildings, PUT /locations/buildings/{id} (BuildingRequest). */
export interface BuildingBody {
  name: string;
  kind: BuildingKind;
}

/** POST /locations/buildings/{id}/floors, PUT /locations/floors/{id} (FloorRequest). */
export interface FloorBody {
  name: string;
  level: number | null;
}

/** POST /locations/buildings/{id}/areas, PUT /locations/areas/{id} (AreaRequest). ``boundary_name``
 * is sent only with the CUSTOM direction. */
export interface AreaBody {
  name: string;
  floor_id: string | null;
  boundary_direction: BoundaryDirection | null;
  boundary_name: string | null;
  boundary_side: BoundarySide | null;
}

export interface Building {
  id: string;
  name: string;
  kind: BuildingKind;
}

export interface Floor {
  id: string;
  building_id: string;
  name: string;
  level: number | null;
}

export interface Area {
  id: string;
  building_id: string;
  floor_id: string | null;
  name: string;
  boundary_direction: BoundaryDirection | null;
  boundary_name: string | null;
  boundary_side: BoundarySide | null;
}

/** The building → optional floor → optional area dropdowns (LocationSelectionRequest). */
export interface LocationSelection {
  building_id: string;
  floor_id: string | null;
  area_id: string | null;
}

/** A location written out from the building down (LocationPathResponse). */
export interface LocationPath {
  level: LocationLevel;
  building_id: string;
  building_name: string;
  building_kind: BuildingKind;
  floor_id: string | null;
  floor_name: string | null;
  area_id: string | null;
  area_name: string | null;
}

export interface LinkedCamera {
  camera_id: string;
  display_name: string;
  location: LocationPath;
}

export interface LinkedGuard {
  guard_id: string;
  full_name: string;
  location: LocationPath;
}

/** GET /locations/{buildings|floors|areas}/{id}/linked (LinkedToLocationResponse). */
export interface LinkedToLocation {
  cameras: LinkedCamera[];
  guards: LinkedGuard[];
}
