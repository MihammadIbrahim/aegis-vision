import { useState } from "react";

import { Field, FormDialog } from "../../../components/ui";
import { NO_MODULES, type AiConfiguration, type ModuleSet } from "../../ai-config/api";
import { LocationPicker, NO_LOCATION, type BuildingNode, type LocationValue } from "../../locations";
import type { Camera, CameraBody, ConnectionType } from "../types";
import { MAX_DISPLAY_NAME, MAX_STREAM_URL, streamUrlProblem } from "../validation";
import { AiFeaturesFieldset } from "./AiFeaturesFieldset";

function initialModules(camera?: Camera): ModuleSet {
  const on = camera?.ai_modules ?? [];
  return { ...NO_MODULES, ...Object.fromEntries(on.map((k) => [k, true])) };
}

function initialLocation(camera?: Camera): LocationValue {
  const l = camera?.location;
  return l ? { building_id: l.building_id, floor_id: l.floor_id ?? "", area_id: l.area_id ?? "" } : NO_LOCATION;
}

/** Add or edit a camera. The location is optional: one building, floor or area of this campus. With the campus's
 * AI configuration (``ai``) it also selects the camera's AI features: none on a new camera (decision D-028). */
export function CameraDialog({
  camera,
  tree,
  ai,
  onClose,
  onSave,
}: {
  camera?: Camera;
  tree: BuildingNode[];
  ai?: AiConfiguration | null;
  onClose: () => void;
  onSave: (body: CameraBody) => Promise<void>;
}) {
  const [name, setName] = useState(camera?.display_name ?? "");
  const [type, setType] = useState<ConnectionType>(camera?.connection_type ?? "RTSP");
  const [url, setUrl] = useState(camera?.stream_url ?? "");
  const [location, setLocation] = useState<LocationValue>(initialLocation(camera));
  const [nameTouched, setNameTouched] = useState(false);
  const [modules, setModules] = useState<ModuleSet>(initialModules(camera));
  const visibleKeys = ai ? ai.modules.map((m) => m.key) : [];
  const urlError = streamUrlProblem(type, url);

  return (
    <FormDialog
      title={camera ? `Edit ${camera.display_name}` : "Add camera"}
      submitLabel={camera ? "Save changes" : "Add camera"}
      onClose={onClose}
      disabled={!name.trim() || urlError !== null}
      onSubmit={() =>
        onSave({
          display_name: name.trim(),
          connection_type: type,
          stream_url: url.trim() || null,
          location: location.building_id
            ? { building_id: location.building_id, floor_id: location.floor_id || null, area_id: location.area_id || null }
            : null,
          ...(ai ? { ai_modules: Object.fromEntries(visibleKeys.map((k) => [k, modules[k]])) } : {}),
        })
      }
    >
      <Field label="Display name" required hint="Unique in this campus." error={nameTouched && !name.trim() ? "Enter a display name." : undefined}>
        <input value={name} maxLength={MAX_DISPLAY_NAME} onChange={(e) => setName(e.target.value)} onBlur={() => setNameTouched(true)} />
      </Field>
      <Field label="Connection type" required>
        <select value={type} onChange={(e) => setType(e.target.value as ConnectionType)}>
          <option value="RTSP">RTSP</option>
          <option value="ONVIF">ONVIF</option>
        </select>
      </Field>
      <Field
        label="Stream URL / IP address"
        hint={`${type === "RTSP" ? "rtsp:// or rtsps://" : "http:// or https://"} address, unique in this campus. Never include a user name or password. You can add it later.`}
        error={urlError ?? undefined}
      >
        <input
          className="mono"
          value={url}
          maxLength={MAX_STREAM_URL}
          autoComplete="off"
          spellCheck={false}
          placeholder={type === "RTSP" ? "rtsp://192.168.1.20:554/stream1" : "http://192.168.1.20/onvif/device_service"}
          onChange={(e) => setUrl(e.target.value)}
        />
      </Field>
      <fieldset>
        <legend>Location (optional)</legend>
        <div className="row picker-row" role="group" aria-label="Camera location">
          <LocationPicker tree={tree} value={location} onChange={setLocation} anyBuildingLabel="No location" />
        </div>
        <p className="hint">Alerts from this camera go to Guards assigned at its location. Indoor or outdoor follows from the building or perimeter.</p>
      </fieldset>
      {ai && <AiFeaturesFieldset ai={ai} value={modules} onChange={setModules} isNew={!camera} />}
    </FormDialog>
  );
}
