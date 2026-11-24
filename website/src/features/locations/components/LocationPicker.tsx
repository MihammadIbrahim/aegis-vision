import type { BuildingNode } from "../types";

export interface LocationValue {
  building_id: string;
  floor_id: string;
  area_id: string;
}

export const NO_LOCATION: LocationValue = { building_id: "", floor_id: "", area_id: "" };

/** Building → optional floor → optional area dropdowns over the campus location tree. Floors
 * appear only for buildings that have them; areas are those of the chosen floor, or those placed
 * directly under the building. */
export function LocationPicker({
  tree,
  value,
  onChange,
  anyBuildingLabel = "Any building",
  idPrefix,
}: {
  tree: BuildingNode[];
  value: LocationValue;
  onChange: (value: LocationValue) => void;
  anyBuildingLabel?: string;
  idPrefix?: string;
}) {
  const building = tree.find((b) => b.id === value.building_id);
  const floor = building?.floors.find((f) => f.id === value.floor_id);
  const areas = floor ? floor.areas : (building?.areas ?? []);
  return (
    <>
      <select
        id={idPrefix ? `${idPrefix}-building` : undefined}
        aria-label="Building"
        value={value.building_id}
        onChange={(e) => onChange({ building_id: e.target.value, floor_id: "", area_id: "" })}
      >
        <option value="">{anyBuildingLabel}</option>
        {tree.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
            {b.kind === "OUTDOOR_PERIMETER" ? " (outdoor)" : ""}
          </option>
        ))}
      </select>
      {building && building.floors.length > 0 && (
        <select aria-label="Floor" value={value.floor_id} onChange={(e) => onChange({ ...value, floor_id: e.target.value, area_id: "" })}>
          <option value="">Whole building</option>
          {building.floors.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      )}
      {building && areas.length > 0 && (
        <select aria-label="Area" value={value.area_id} onChange={(e) => onChange({ ...value, area_id: e.target.value })}>
          <option value="">{floor ? "Whole floor" : "Any area"}</option>
          {areas.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      )}
    </>
  );
}
