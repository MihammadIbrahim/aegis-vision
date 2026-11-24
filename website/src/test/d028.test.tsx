// Decision D-028: explicit per-camera AI features (none by default), AI pills, multi-camera search and bulk 1-click
// assignment, the Admin's Supervisor AI feature access panel, the Supervisor's restricted view and organization
// blocks. Mocked URLs and bodies are the Core API's (camera_router.py, ai_config_router.py).
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AiConfigurationPage } from "../features/ai-config/pages/AiConfigurationPage";
import type { AiConfiguration, CameraAi, ModuleSet } from "../features/ai-config/api";
import type { Camera } from "../features/cameras";
import { CamerasPage } from "../features/cameras/pages/CamerasPage";
import { mockApi, renderAs, sent } from "./helpers";

const ALL: ModuleSet = { human_detection: true, person_reid: true, weapon_detection: true, fight_detection: true, face_recognition: true };
const NONE: ModuleSet = { human_detection: false, person_reid: false, weapon_detection: false, fight_detection: false, face_recognition: false };
const CORE: ModuleSet = { ...ALL, face_recognition: false };
const LABELS = {
  human_detection: "Human detection & single-camera tracking",
  person_reid: "Cross-camera person re-identification (Re-ID)",
  weapon_detection: "Weapon detection",
  fight_detection: "Fight / violence detection",
  face_recognition: "Restricted-area face recognition",
} as const;
type Key = keyof typeof LABELS;

const cameraAi = (id: string, name: string, configured: ModuleSet): CameraAi => ({
  camera_id: id, camera_name: name, location: null, configured, effective: configured, customized: true,
  stream_state: "ONLINE", running: [], running_now: false, frames_per_second: null, last_error: null, reported_at: null,
  updated_by_name: null, updated_at: null,
});

function config(over: Partial<AiConfiguration> = {}, keys: Key[] = Object.keys(LABELS) as Key[]): AiConfiguration {
  return {
    modules: keys.map((key) => ({ key, label: LABELS[key] })),
    platform: CORE, campus: CORE, campus_permitted: CORE, campus_customized: false, campus_updated_at: null,
    campus_updated_by_name: null,
    alert_rules: [{ detection: "WEAPON", priority: "HIGH", module: "weapon_detection" }],
    cameras: [cameraAi("c1", "Gate Camera", { ...NONE, weapon_detection: true }), cameraAi("c2", "Lobby Camera", NONE)],
    available_modules: ["human_detection", "person_reid", "weapon_detection", "fight_detection"],
    org_blocked: NONE, blocked_by_platform: [], supervisor_visible_modules: ALL,
    ...over,
  };
}

const camera = (id: string, name: string, ai: Camera["ai_modules"] = [], extra: Partial<Camera> = {}): Camera => ({
  id, campus_id: "k1", display_name: name, connection_type: "RTSP", stream_url: `rtsp://10.0.0.5/${id}`, environment: null,
  location: null, created_at: null, updated_at: null, ai_modules: ai, ai_effective_modules: ai, ...extra,
});
const CAMERAS = [
  camera("c1", "Gate Camera", ["weapon_detection"]),
  camera("c2", "Lobby Camera"),
  camera("c3", "Gate North", ["human_detection", "fight_detection"], { ai_effective_modules: ["human_detection"] }),
];
const treeRoute = { method: "GET", url: /\/locations$/, body: [] };
const aiRoute = (body: AiConfiguration) => ({ method: "GET", url: /\/ai-configuration$/, body });

describe("Add / Edit Camera: AI features", () => {
  it("adds a camera with no AI feature selected by default and says so", async () => {
    const calls = mockApi([treeRoute, aiRoute(config()), { method: "GET", url: /\/v1\/cameras(\?.*)?$/, body: CAMERAS },
      { method: "POST", url: /\/cameras$/, status: 201, body: camera("c9", "Yard") }]);
    renderAs("ADMIN", <CamerasPage />);
    await screen.findByText("Gate Camera");
    await screen.findByRole("region", { name: "Bulk AI assignment" });
    fireEvent.click(screen.getByRole("button", { name: "Add camera" }));
    const dialog = screen.getByRole("dialog", { name: "Add camera" });
    expect(within(dialog).getByText("No AI features are enabled by default. Select the features you want to run on this camera.")).toBeTruthy();
    const boxes = within(within(dialog).getByTestId("camera-ai-features")).getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([false, false, false, false, false]);
    // Face recognition is disabled by the platform: shown with the reason, and cannot be selected.
    expect(within(dialog).getByText(/Disabled by the platform/)).toBeTruthy();
    expect(boxes[4].disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/^Display name/), { target: { value: "Yard" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add camera" }));
    await waitFor(() => expect(sent(calls, "POST", /\/cameras$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/cameras$/)[0].body).toEqual({
      display_name: "Yard", connection_type: "RTSP", stream_url: null, location: null,
      ai_modules: { ...NONE },
    });
  });

  it("pre-fills the camera's features when editing and sends the changes", async () => {
    const calls = mockApi([treeRoute, aiRoute(config()), { method: "GET", url: /\/v1\/cameras(\?.*)?$/, body: CAMERAS },
      { method: "PUT", url: /\/cameras\/c1$/, body: CAMERAS[0] }]);
    renderAs("SUPERVISOR", <CamerasPage />);
    await screen.findByRole("region", { name: "Bulk AI assignment" });
    fireEvent.click(await screen.findByRole("button", { name: "Edit Gate Camera" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Gate Camera" });
    expect(within(dialog).queryByText(/No AI features are enabled by default/)).toBeNull();
    const weapon = within(dialog).getByLabelText(/^Weapon detection/) as HTMLInputElement;
    expect(weapon.checked).toBe(true);
    fireEvent.click(within(dialog).getByLabelText(/^Fight \/ violence detection/));
    fireEvent.click(weapon);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/cameras\/c1$/)).toHaveLength(1));
    expect((sent(calls, "PUT", /\/cameras\/c1$/)[0].body as { ai_modules: unknown }).ai_modules).toEqual({ ...NONE, fight_detection: true });
  });

  it("shows each camera's features as pills (dashed when selected but not running)", async () => {
    mockApi([treeRoute, aiRoute(config()), { method: "GET", url: /\/v1\/cameras(\?.*)?$/, body: CAMERAS }]);
    renderAs("ADMIN", <CamerasPage />);
    const table = await screen.findByRole("table", { name: "Cameras" });
    expect(within(table).getByText("Weapons").className).toBe("ai-pill");
    expect(within(table).getByText("No AI")).toBeTruthy();
    expect(within(table).getByText("Fights").className).toBe("ai-pill off");
  });
});

describe("Multi-camera search and bulk AI assignment", () => {
  it("searches, selects all visible cameras and adds features to them in one step", async () => {
    const matching = [CAMERAS[0], CAMERAS[2]];
    const calls = mockApi([
      treeRoute,
      aiRoute(config()),
      { method: "GET", url: /\/v1\/cameras\?q=gate$/, body: matching },
      { method: "GET", url: /\/v1\/cameras$/, body: CAMERAS },
      { method: "PUT", url: /\/ai-configuration\/cameras\/bulk$/, body: { mode: "ADD", updated_count: 2, cameras: [] } },
    ]);
    renderAs("ADMIN", <CamerasPage />);
    await screen.findByText("Lobby Camera");
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "gate" } });
    await waitFor(() => expect(screen.queryByText("Lobby Camera")).toBeNull());
    expect(screen.getByText("0 cameras selected")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Assign AI Features (Bulk)" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Select all visible"));
    expect(screen.getByText("2 cameras selected")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Select Gate North"));
    expect(screen.getByText("1 camera selected")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Select Gate North"));
    fireEvent.click(screen.getByRole("button", { name: "Assign AI Features (Bulk)" }));
    const dialog = screen.getByRole("dialog", { name: "Assign AI features to 2 cameras" });
    expect((within(dialog).getByLabelText("Add / Enable") as HTMLInputElement).checked).toBe(true);
    const apply = within(dialog).getByRole("button", { name: "Apply to selected cameras" }) as HTMLButtonElement;
    fireEvent.click(within(dialog).getByLabelText(/^Weapon detection/));
    fireEvent.click(within(dialog).getByLabelText(/^Cross-camera person/));
    expect(apply.disabled).toBe(true); // the password is required
    fireEvent.change(within(dialog).getByLabelText(/Your current password/), { target: { value: "Admin-pass-2026" } });
    fireEvent.click(apply);
    await waitFor(() => expect(sent(calls, "PUT", /\/bulk$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/bulk$/)[0].body).toEqual({
      camera_ids: ["c1", "c3"], mode: "ADD", modules: { weapon_detection: true, person_reid: true }, current_password: "Admin-pass-2026",
    });
    expect(await screen.findByText("AI features updated on 2 cameras.")).toBeTruthy();
    expect(screen.getByText("0 cameras selected")).toBeTruthy();
  });

  it("removes or replaces features and shows the server's refusal in the dialog", async () => {
    const calls = mockApi([
      treeRoute,
      aiRoute(config()),
      { method: "GET", url: /\/v1\/cameras(\?.*)?$/, body: CAMERAS },
      { method: "PUT", url: /\/bulk$/, status: 404, body: { detail: "Camera not found.", code: "CAMERA_NOT_FOUND" } },
    ]);
    renderAs("SUPERVISOR", <CamerasPage />);
    await screen.findByText("Lobby Camera");
    fireEvent.click(screen.getByLabelText("Select Gate Camera"));
    fireEvent.click(screen.getByLabelText("Select Lobby Camera"));
    fireEvent.click(screen.getByRole("button", { name: "Assign AI Features (Bulk)" }));
    const dialog = screen.getByRole("dialog", { name: "Assign AI features to 2 cameras" });
    fireEvent.click(within(dialog).getByLabelText("Replace"));
    expect(within(dialog).getByText(/Each camera runs exactly the selected features/)).toBeTruthy();
    fireEvent.click(within(dialog).getByLabelText(/^Human detection/));
    fireEvent.change(within(dialog).getByLabelText(/Your current password/), { target: { value: "pw" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply to selected cameras" }));
    expect(await within(dialog).findByText("Camera not found.")).toBeTruthy();
    expect(sent(calls, "PUT", /\/bulk$/)[0].body).toMatchObject({ camera_ids: ["c1", "c2"], mode: "SET", modules: { human_detection: true } });
  });
});

describe("AI Configuration: Supervisor access and organization blocks", () => {
  it("lets an Admin hide a feature from Supervisors, with the password", async () => {
    const calls = mockApi([aiRoute(config()), { method: "PUT", url: /\/ai-configuration\/campus$/, body: config() }]);
    renderAs("ADMIN", <AiConfigurationPage />);
    const panel = await screen.findByTestId("supervisor-access");
    expect(within(panel).getByText("Supervisor AI Feature Access")).toBeTruthy();
    expect(within(panel).getByText(/Disabled by the platform: nobody can use it/)).toBeTruthy();
    const save = within(panel).getByRole("button", { name: "Save Supervisor access" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(within(panel).getByLabelText("Visible to Supervisors: Weapon detection"));
    fireEvent.click(save);
    const dialog = screen.getByRole("dialog", { name: "Save Supervisor AI feature access" });
    fireEvent.change(within(dialog).getByLabelText(/Your current password/), { target: { value: "Admin-pass-2026" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/campus$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/campus$/)[0].body).toEqual({
      ...CORE, supervisor_visible_modules: { ...ALL, weapon_detection: false }, current_password: "Admin-pass-2026",
    });
    expect(await screen.findByText("Supervisor AI feature access saved.")).toBeTruthy();
  });

  it("shows a Supervisor only the features visible to them, with no access panel", async () => {
    const keys: Key[] = ["human_detection", "person_reid", "fight_detection", "face_recognition"];
    mockApi([aiRoute(config({ supervisor_visible_modules: null, available_modules: ["human_detection", "person_reid", "fight_detection"],
      alert_rules: [], cameras: [cameraAi("c1", "Gate Camera", NONE)] }, keys))]);
    renderAs("SUPERVISOR", <AiConfigurationPage />);
    await screen.findByText("Gate Camera");
    expect(screen.queryByTestId("supervisor-access")).toBeNull();
    expect(screen.queryByText("Weapon detection")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Modules" }));
    const dialog = screen.getByRole("dialog", { name: "AI modules of Gate Camera" });
    expect(within(dialog).getAllByRole("checkbox")).toHaveLength(4);
    expect(within(dialog).queryByText(/Weapon/)).toBeNull();
  });

  it("marks features the Super Admin blocked for the organization and never re-sends them", async () => {
    const blocked = config({
      org_blocked: { ...NONE, weapon_detection: true }, blocked_by_platform: ["weapon_detection"],
      campus_permitted: { ...CORE, weapon_detection: false }, available_modules: ["human_detection", "person_reid", "fight_detection"],
    });
    const calls = mockApi([aiRoute(blocked), { method: "PUT", url: /\/campus$/, body: blocked }]);
    renderAs("ADMIN", <AiConfigurationPage />);
    expect(await screen.findByText(/Blocked for your organization by the platform: Weapons/)).toBeTruthy();
    expect(screen.getByText("Blocked by the platform")).toBeTruthy();
    expect(screen.getByText(/Blocked for your organization by the platform: nobody on this campus can use it/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Change campus modules" }));
    const dialog = screen.getByRole("dialog", { name: "Campus AI modules" });
    const weapon = within(dialog).getByLabelText(/^Weapon detection/) as HTMLInputElement;
    expect(weapon.checked).toBe(false);
    expect(weapon.disabled).toBe(true);
    expect(within(dialog).getByText(/blocked for your organization by the platform/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/Your current password/), { target: { value: "pw" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/campus$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/campus$/)[0].body).toEqual({ ...CORE, weapon_detection: false, current_password: "pw" });
  });
});
