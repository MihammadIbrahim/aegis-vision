// Phase 7: Buildings & Locations and Cameras for Admins and Supervisors. The mocked URLs, methods and
// bodies are the Core API's (location_router.py, camera_router.py), so these tests also pin the contract.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "../app/App";
import { StaticAuthProvider } from "../auth/AuthContext";
import { canManageBuildings, canManageSite } from "../auth/permissions";
import { ToastProvider } from "../components/ui";
import type { Camera } from "../features/cameras";
import { CamerasPage } from "../features/cameras/pages/CamerasPage";
import { CREDENTIALS_MESSAGE, streamUrlProblem } from "../features/cameras/validation";
import { boundaryLabel } from "../features/locations/labels";
import { LocationsPage } from "../features/locations/pages/LocationsPage";
import { mockApi, renderAs, sent, sessionFor } from "./helpers";

const area = (id: string, name: string, floor_id: string | null, extra: Record<string, unknown> = {}) => ({
  id, building_id: "b1", floor_id, name, boundary_direction: null, boundary_name: null, boundary_side: null,
  camera_count: 0, guard_count: 0, ...extra,
});

const TREE = [
  {
    id: "b1", name: "Admin Block", kind: "INDOOR", camera_count: 2, guard_count: 1,
    floors: [{
      id: "f1", building_id: "b1", name: "Ground", level: 0, camera_count: 2, guard_count: 1,
      areas: [
        area("a1", "Server Room", "f1", { boundary_direction: "NORTH", boundary_side: "INSIDE", camera_count: 1, guard_count: 1 }),
        area("a2", "Lobby", "f1"),
      ],
    }],
    areas: [area("a3", "Roof", null, { boundary_direction: "CUSTOM", boundary_name: "Rooftop edge", boundary_side: "OUTSIDE" })],
  },
  { id: "b2", name: "North Fence", kind: "OUTDOOR_PERIMETER", camera_count: 0, guard_count: 0, floors: [], areas: [] },
];

const PATH = { level: "AREA", building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR", floor_id: "f1", floor_name: "Ground", area_id: "a1", area_name: "Server Room" };

const CAMERAS: Camera[] = [
  { id: "c1", campus_id: "k1", display_name: "Gate Camera", connection_type: "RTSP", stream_url: "rtsp://10.0.0.5:554/s1",
    environment: "INDOOR", location: PATH as Camera["location"], created_at: null, updated_at: null },
  { id: "c2", campus_id: "k1", display_name: "Spare Camera", connection_type: "ONVIF", stream_url: null,
    environment: null, location: null, created_at: null, updated_at: null },
];

const treeRoute = { method: "GET", url: /\/locations$/, body: TREE };

describe("site permissions", () => {
  it("lets only Admins change buildings, and both roles manage floors, areas and cameras", () => {
    expect(canManageBuildings("ADMIN")).toBe(true);
    expect(canManageBuildings("SUPERVISOR")).toBe(false);
    expect(canManageSite("SUPERVISOR")).toBe(true);
    expect(canManageSite("GUARD")).toBe(false);
  });

  it("opens both pages for a Supervisor through the app routes", async () => {
    mockApi([treeRoute, { method: "GET", url: /\/v1\/cameras$/, body: [] }, { method: "GET", url: /\/live\/cameras$/, body: [] }, [/\/notifications/, []]]);
    render(
      <MemoryRouter initialEntries={["/locations"]}>
        <StaticAuthProvider session={sessionFor("SUPERVISOR")}>
          <ToastProvider>
            <App />
          </ToastProvider>
        </StaticAuthProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: "Buildings & Locations", level: 1 })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: /^Cameras$/ }));
    expect(await screen.findByRole("heading", { name: "Cameras", level: 1 })).toBeTruthy();
  });
});

describe("Buildings & Locations", () => {
  it("shows the tree with kinds, floors, areas, boundaries and counts", async () => {
    mockApi([treeRoute]);
    renderAs("ADMIN", <LocationsPage />);
    expect(await screen.findByRole("heading", { name: "Admin Block" })).toBeTruthy();
    expect(screen.getByText("Outdoor perimeter")).toBeTruthy();
    expect(screen.getByText("Server Room")).toBeTruthy();
    expect(screen.getByText("Boundary: North · Inside")).toBeTruthy();
    expect(screen.getByText("Boundary: Rooftop edge · Outside")).toBeTruthy();
    expect(screen.getByText("Areas not on a floor")).toBeTruthy();
    expect(screen.getByText("1 floor · 3 areas · 2 cameras · 1 Guard")).toBeTruthy();
    expect(screen.getByText("No areas yet.")).toBeTruthy();
    // No floors can be added to an outdoor perimeter.
    expect(screen.queryByRole("button", { name: "Add floor to North Fence" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add floor to Admin Block" })).toBeTruthy();
  });

  it("collapses and expands a building", async () => {
    mockApi([treeRoute]);
    renderAs("ADMIN", <LocationsPage />);
    const toggle = await screen.findByRole("button", { name: "Collapse Admin Block" });
    fireEvent.click(toggle);
    expect(screen.queryByText("Server Room")).toBeNull();
    expect(screen.getByRole("button", { name: "Expand Admin Block" }).getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "Expand Admin Block" }));
    expect(screen.getByText("Server Room")).toBeTruthy();
  });

  it("gives Supervisors no building actions, but floor and area actions", async () => {
    mockApi([treeRoute]);
    renderAs("SUPERVISOR", <LocationsPage />);
    await screen.findByRole("heading", { name: "Admin Block" });
    expect(screen.queryByRole("button", { name: "Add building or perimeter" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit Admin Block" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove Admin Block" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove North Fence" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add floor to Admin Block" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Edit floor Ground" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove area Lobby" })).toBeTruthy();
    expect(screen.getByText(/Buildings and outdoor perimeters are managed by Organization Admins/)).toBeTruthy();
  });

  it("lets an Admin add a perimeter (POST /locations/buildings)", async () => {
    const calls = mockApi([treeRoute, { method: "POST", url: /\/locations\/buildings$/, status: 201, body: { id: "b3", name: "East Wall", kind: "OUTDOOR_PERIMETER" } }]);
    renderAs("ADMIN", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Add building or perimeter" }));
    const dialog = screen.getByRole("dialog", { name: "Add building or perimeter" });
    const add = within(dialog).getByRole("button", { name: "Add" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: "  East Wall " } });
    fireEvent.change(within(dialog).getByLabelText(/^Type/), { target: { value: "OUTDOOR_PERIMETER" } });
    fireEvent.click(add);
    await waitFor(() => expect(sent(calls, "POST", /\/locations\/buildings$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/locations\/buildings$/)[0].body).toEqual({ name: "East Wall", kind: "OUTDOOR_PERIMETER" });
    expect(await screen.findByText("East Wall added.")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Add building or perimeter" })).toBeNull();
    expect(sent(calls, "GET", /\/locations$/).length).toBeGreaterThanOrEqual(2);
  });

  it("lets an Admin rename a building (PUT /locations/buildings/{id}) and shows server errors in the dialog", async () => {
    const calls = mockApi([
      treeRoute,
      { method: "PUT", url: /\/locations\/buildings\/b1$/, status: 409, body: { detail: "A building or perimeter with this name already exists here.", code: "LOCATION_NAME_TAKEN" } },
    ]);
    renderAs("ADMIN", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Admin Block" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Admin Block" });
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: "North Fence" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await within(dialog).findByText("A building or perimeter with this name already exists here.")).toBeTruthy();
    expect(sent(calls, "PUT", /\/locations\/buildings\/b1$/)[0].body).toEqual({ name: "North Fence", kind: "INDOOR" });
    expect(screen.getByRole("dialog", { name: "Edit Admin Block" })).toBeTruthy();
  });

  it("lets a Supervisor add a floor (POST /locations/buildings/{id}/floors) with a checked level", async () => {
    const calls = mockApi([treeRoute, { method: "POST", url: /\/locations\/buildings\/b1\/floors$/, status: 201, body: { id: "f2", building_id: "b1", name: "Basement", level: -1 } }]);
    renderAs("SUPERVISOR", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Add floor to Admin Block" }));
    const dialog = screen.getByRole("dialog", { name: "Add floor to Admin Block" });
    fireEvent.change(within(dialog).getByLabelText(/^Floor name/), { target: { value: "Basement" } });
    fireEvent.change(within(dialog).getByLabelText(/^Level/), { target: { value: "500" } });
    expect(within(dialog).getByText(/Enter a whole number from -20 to 300/)).toBeTruthy();
    expect((within(dialog).getByRole("button", { name: "Add floor" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/^Level/), { target: { value: "-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add floor" }));
    await waitFor(() => expect(sent(calls, "POST", /\/floors$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/floors$/)[0].body).toEqual({ name: "Basement", level: -1 });
  });

  it("edits a floor with an empty level as null (PUT /locations/floors/{id})", async () => {
    const calls = mockApi([treeRoute, { method: "PUT", url: /\/locations\/floors\/f1$/, body: { id: "f1", building_id: "b1", name: "Ground", level: null } }]);
    renderAs("SUPERVISOR", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit floor Ground" }));
    const dialog = screen.getByRole("dialog", { name: "Edit floor Ground" });
    fireEvent.change(within(dialog).getByLabelText(/^Level/), { target: { value: "" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/floors\/f1$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/floors\/f1$/)[0].body).toEqual({ name: "Ground", level: null });
  });

  it("adds an area on a floor with a custom boundary (POST /locations/buildings/{id}/areas)", async () => {
    const calls = mockApi([treeRoute, { method: "POST", url: /\/locations\/buildings\/b1\/areas$/, status: 201, body: {} }]);
    renderAs("SUPERVISOR", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Add area to floor Ground" }));
    const dialog = screen.getByRole("dialog", { name: "Add area to Admin Block" });
    expect((within(dialog).getByLabelText(/^Floor/) as HTMLSelectElement).value).toBe("f1");
    fireEvent.change(within(dialog).getByLabelText(/^Area \/ zone name/), { target: { value: "Loading Bay" } });
    fireEvent.change(within(dialog).getByLabelText(/^Direction/), { target: { value: "CUSTOM" } });
    const add = within(dialog).getByRole("button", { name: "Add area" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true); // a custom boundary needs a name
    fireEvent.change(within(dialog).getByLabelText(/^Boundary name/), { target: { value: "Dock side" } });
    fireEvent.change(within(dialog).getByLabelText(/^Inside \/ outside/), { target: { value: "OUTSIDE" } });
    fireEvent.click(add);
    await waitFor(() => expect(sent(calls, "POST", /\/areas$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/areas$/)[0].body).toEqual({
      name: "Loading Bay", floor_id: "f1", boundary_direction: "CUSTOM", boundary_name: "Dock side", boundary_side: "OUTSIDE",
    });
  });

  it("edits an area: a compass direction sends no boundary name (PUT /locations/areas/{id})", async () => {
    const calls = mockApi([treeRoute, { method: "PUT", url: /\/locations\/areas\/a3$/, body: {} }]);
    renderAs("ADMIN", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit area Roof" }));
    const dialog = screen.getByRole("dialog", { name: "Edit area Roof" });
    expect((within(dialog).getByLabelText(/^Boundary name/) as HTMLInputElement).value).toBe("Rooftop edge");
    fireEvent.change(within(dialog).getByLabelText(/^Direction/), { target: { value: "WEST" } });
    expect(within(dialog).queryByLabelText(/^Boundary name/)).toBeNull();
    fireEvent.change(within(dialog).getByLabelText(/^Floor/), { target: { value: "f1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/areas\/a3$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/areas\/a3$/)[0].body).toEqual({
      name: "Roof", floor_id: "f1", boundary_direction: "WEST", boundary_name: null, boundary_side: "OUTSIDE",
    });
  });

  it("does not offer to remove a location that still has cameras, Guards or sub-locations", async () => {
    const calls = mockApi([treeRoute]);
    renderAs("ADMIN", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove area Server Room" }));
    const dialog = screen.getByRole("dialog", { name: "Server Room cannot be removed yet" });
    expect(within(dialog).getByText("It still has 1 camera, 1 assigned Guard. Move or remove them first.")).toBeTruthy();
    fireEvent.click(within(dialog).getAllByRole("button", { name: "Close" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Remove Admin Block" }));
    expect(screen.getByText("It still has 1 floor, 3 areas, 2 cameras, 1 assigned Guard. Move or remove them first.")).toBeTruthy();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  });

  it("removes an empty area after confirmation (DELETE /locations/areas/{id})", async () => {
    const calls = mockApi([treeRoute, { method: "DELETE", url: /\/locations\/areas\/a2$/, status: 204, body: null }]);
    renderAs("SUPERVISOR", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove area Lobby" }));
    const dialog = screen.getByRole("dialog", { name: "Remove area Lobby?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove area" }));
    await waitFor(() => expect(sent(calls, "DELETE", /\/locations\/areas\/a2$/)).toHaveLength(1));
    expect(await screen.findByText("Lobby removed.")).toBeTruthy();
  });

  it("removes an empty perimeter as an Admin (DELETE /locations/buildings/{id}) and reports a refusal", async () => {
    const calls = mockApi([
      treeRoute,
      { method: "DELETE", url: /\/locations\/buildings\/b2$/, status: 409, body: { detail: "This location still has 1 camera. Move or remove them first.", code: "LOCATION_NOT_EMPTY" } },
    ]);
    renderAs("ADMIN", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove North Fence" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Remove perimeter North Fence?" })).getByRole("button", { name: "Remove perimeter" }));
    expect(await screen.findByText("This location still has 1 camera. Move or remove them first.")).toBeTruthy();
    expect(sent(calls, "DELETE", /\/buildings\/b2$/)).toHaveLength(1);
  });

  it("lists the cameras and Guards linked to a location (GET /locations/areas/{id}/linked)", async () => {
    const calls = mockApi([
      treeRoute,
      { method: "GET", url: /\/locations\/areas\/a1\/linked$/, body: {
        cameras: [{ camera_id: "c1", display_name: "Gate Camera", location: PATH }],
        guards: [{ guard_id: "GRDABC123", full_name: "Bilal Guard", location: PATH }],
      } },
    ]);
    renderAs("SUPERVISOR", <LocationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Cameras and Guards linked to Server Room" }));
    const dialog = screen.getByRole("dialog", { name: "Linked to Server Room" });
    expect(await within(dialog).findByText("Gate Camera")).toBeTruthy();
    expect(within(dialog).getByText("GRDABC123")).toBeTruthy();
    expect(within(dialog).getByText("Bilal Guard")).toBeTruthy();
    expect(within(dialog).getAllByText("Admin Block › Ground › Server Room")).toHaveLength(2);
    expect(sent(calls, "GET", /\/linked$/)).toHaveLength(1);
  });

  it("shows an empty state and an error state", async () => {
    mockApi([{ method: "GET", url: /\/locations$/, body: [] }]);
    const { unmount } = renderAs("SUPERVISOR", <LocationsPage />);
    expect(await screen.findByText("No buildings or perimeters yet.")).toBeTruthy();
    expect(screen.getByText("An Organization Admin adds buildings and perimeters.")).toBeTruthy();
    unmount();
    mockApi([{ method: "GET", url: /\/locations$/, status: 503, body: { detail: "Service unavailable." } }]);
    renderAs("ADMIN", <LocationsPage />);
    expect(await screen.findByText("Service unavailable.")).toBeTruthy();
  });

  it("describes boundaries in words", () => {
    expect(boundaryLabel({ boundary_direction: "EAST", boundary_name: null, boundary_side: null })).toBe("East");
    expect(boundaryLabel({ boundary_direction: null, boundary_name: null, boundary_side: "INSIDE" })).toBe("Inside");
    expect(boundaryLabel({ boundary_direction: null, boundary_name: null, boundary_side: null })).toBeNull();
  });
});

describe("stream URL check (mirrors the server's camera.py)", () => {
  it("refuses embedded credentials, wrong schemes, bad ports and hosts; allows an empty URL", () => {
    expect(streamUrlProblem("RTSP", "")).toBeNull();
    expect(streamUrlProblem("RTSP", "rtsp://192.168.1.20:554/stream1")).toBeNull();
    expect(streamUrlProblem("RTSP", "rtsps://cam-1.example.org/live")).toBeNull();
    expect(streamUrlProblem("ONVIF", "http://[fe80::1]:80/onvif")).toBeNull();
    expect(streamUrlProblem("RTSP", "rtsp://admin:secret@192.168.1.20/s")).toBe(CREDENTIALS_MESSAGE);
    expect(streamUrlProblem("ONVIF", "https://admin@cam.example.org/onvif")).toBe(CREDENTIALS_MESSAGE);
    expect(streamUrlProblem("ONVIF", "rtsp://10.0.0.1/s")).toBe("A ONVIF camera needs a http:// or https:// address.");
    expect(streamUrlProblem("RTSP", "http://10.0.0.1/s")).toBe("A RTSP camera needs a rtsp:// or rtsps:// address.");
    expect(streamUrlProblem("RTSP", "rtsp://10.0.0.1:99999/s")).toBe("The port in the stream URL is not valid.");
    expect(streamUrlProblem("RTSP", "rtsp://bad_host!/s")).toBe("The stream URL needs a valid IP address or host name.");
    expect(streamUrlProblem("RTSP", "rtsp://10.0.0.1/a b")).toBe("The stream URL is too long or contains spaces.");
  });
});

describe("Cameras", () => {
  const listRoute = { method: "GET", url: /\/v1\/cameras(\?.*)?$/, body: CAMERAS };
  // GET /live/cameras: online/offline status per camera (decision D-023).
  const status = (state: string, last_error: string | null = null) => ({
    state, last_error, last_seen_at: null, source: "INGESTION", state_changed_at: null, reported_at: null,
  });
  const liveRoute = {
    method: "GET",
    url: /\/live\/cameras$/,
    body: [
      { camera: CAMERAS[0], status: status("ONLINE") },
      { camera: CAMERAS[1], status: status("OFFLINE", "The connection was refused.") },
    ],
  };

  it("lists cameras with ID, connection, stream URL, environment, location and online/offline status", async () => {
    mockApi([treeRoute, liveRoute, listRoute]);
    renderAs("SUPERVISOR", <CamerasPage />);
    const table = await screen.findByRole("table", { name: "Cameras" });
    expect(within(table).getByText("Gate Camera")).toBeTruthy();
    expect(within(table).getByText("c1")).toBeTruthy();
    expect(within(table).getByText("rtsp://10.0.0.5:554/s1")).toBeTruthy();
    expect(within(table).getByText("Indoor")).toBeTruthy();
    expect(within(table).getByText("Admin Block › Ground › Server Room")).toBeTruthy();
    expect(within(table).getByText("ONVIF")).toBeTruthy();
    expect(within(table).getByText("Not set")).toBeTruthy();
    expect(within(table).getByText("No location")).toBeTruthy();
    expect(screen.getByText("2 cameras.")).toBeTruthy();
    expect(await within(table).findByText("Online")).toBeTruthy();
    expect(within(table).getByText("Offline")).toBeTruthy();
    expect(within(table).getByText("The connection was refused.")).toBeTruthy();
    // The connection test needs a stream URL; the camera without one cannot be tested.
    expect((screen.getByRole("button", { name: "Test connection of Gate Camera" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Test connection of Spare Camera" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Add camera" })).toBeTruthy();
  });

  it("tests a camera's connection (POST /live/cameras/{id}/health-check) and shows the result", async () => {
    const calls = mockApi([
      treeRoute,
      liveRoute,
      listRoute,
      {
        method: "POST",
        url: /\/live\/cameras\/c1\/health-check$/,
        body: { reachable: false, checked_at: "2026-11-16T10:00:00Z", latency_ms: 3, detail: "The connection was refused.", camera: { camera: CAMERAS[0], status: status("OFFLINE", "The connection was refused.") } },
      },
    ]);
    renderAs("SUPERVISOR", <CamerasPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Test connection of Gate Camera" }));
    await waitFor(() => expect(sent(calls, "POST", /health-check$/)).toHaveLength(1));
    expect(await screen.findByText("Gate Camera: The connection was refused.")).toBeTruthy();
    // The statuses are read again after the test.
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && /\/live\/cameras$/.test(c.url)).length).toBe(2));
  });

  it("searches as you type and filters by location (GET /cameras?q=&building_id=&floor_id=)", async () => {
    const calls = mockApi([treeRoute, listRoute]);
    renderAs("ADMIN", <CamerasPage />);
    await screen.findByText("Gate Camera");
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "gate" } });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/cameras?q=gate"))).toBe(true));
    await waitFor(() => expect((screen.getByLabelText("Building") as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(screen.getByLabelText("Building"), { target: { value: "b1" } });
    fireEvent.change(screen.getByLabelText("Floor"), { target: { value: "f1" } });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/cameras?q=gate&building_id=b1&floor_id=f1"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(calls[calls.length - 1].url).toMatch(/\/cameras$/));
  });

  it("adds a camera with a location (POST /cameras)", async () => {
    const calls = mockApi([treeRoute, listRoute, { method: "POST", url: /\/cameras$/, status: 201, body: CAMERAS[0] }]);
    renderAs("SUPERVISOR", <CamerasPage />);
    await screen.findByText("Gate Camera");
    fireEvent.click(screen.getByRole("button", { name: "Add camera" }));
    const dialog = screen.getByRole("dialog", { name: "Add camera" });
    fireEvent.change(within(dialog).getByLabelText(/^Display name/), { target: { value: " Lobby Cam " } });
    fireEvent.change(within(dialog).getByLabelText(/^Stream URL/), { target: { value: " rtsp://10.0.0.9:554/live " } });
    fireEvent.change(within(dialog).getByLabelText("Building"), { target: { value: "b1" } });
    fireEvent.change(within(dialog).getByLabelText("Floor"), { target: { value: "f1" } });
    fireEvent.change(within(dialog).getByLabelText("Area"), { target: { value: "a2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add camera" }));
    await waitFor(() => expect(sent(calls, "POST", /\/cameras$/)).toHaveLength(1));
    expect(sent(calls, "POST", /\/cameras$/)[0].body).toEqual({
      display_name: "Lobby Cam", connection_type: "RTSP", stream_url: "rtsp://10.0.0.9:554/live",
      location: { building_id: "b1", floor_id: "f1", area_id: "a2" },
    });
    expect(await screen.findByText("Lobby Cam added.")).toBeTruthy();
  });

  it("never sends a stream URL with credentials", async () => {
    const calls = mockApi([treeRoute, listRoute]);
    renderAs("ADMIN", <CamerasPage />);
    await screen.findByText("Gate Camera");
    fireEvent.click(screen.getByRole("button", { name: "Add camera" }));
    const dialog = screen.getByRole("dialog", { name: "Add camera" });
    fireEvent.change(within(dialog).getByLabelText(/^Display name/), { target: { value: "Yard" } });
    const url = within(dialog).getByLabelText(/^Stream URL/);
    fireEvent.change(url, { target: { value: "rtsp://admin:1234@10.0.0.9/live" } });
    expect(within(dialog).getByText(CREDENTIALS_MESSAGE)).toBeTruthy();
    expect(url.getAttribute("aria-invalid")).toBe("true");
    const add = within(dialog).getByRole("button", { name: "Add camera" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.click(add);
    fireEvent.change(within(dialog).getByLabelText(/^Connection type/), { target: { value: "ONVIF" } });
    fireEvent.change(url, { target: { value: "rtsp://10.0.0.9/live" } });
    expect(within(dialog).getByText("A ONVIF camera needs a http:// or https:// address.")).toBeTruthy();
    expect(sent(calls, "POST", /\/cameras$/)).toHaveLength(0);
  });

  it("edits a camera and removes its location with location: null (PUT /cameras/{id})", async () => {
    const calls = mockApi([treeRoute, listRoute, { method: "PUT", url: /\/cameras\/c1$/, body: CAMERAS[0] }]);
    renderAs("ADMIN", <CamerasPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Gate Camera" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Gate Camera" });
    await waitFor(() => expect((within(dialog).getByLabelText("Area") as HTMLSelectElement).value).toBe("a1"));
    fireEvent.change(within(dialog).getByLabelText("Building"), { target: { value: "" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(sent(calls, "PUT", /\/cameras\/c1$/)).toHaveLength(1));
    expect(sent(calls, "PUT", /\/cameras\/c1$/)[0].body).toEqual({
      display_name: "Gate Camera", connection_type: "RTSP", stream_url: "rtsp://10.0.0.5:554/s1", location: null,
    });
  });

  it("shows the server's refusal (duplicate name) inside the dialog", async () => {
    mockApi([treeRoute, listRoute, { method: "PUT", url: /\/cameras\/c2$/, status: 409, body: { detail: "A camera with this display name already exists in this campus.", code: "CAMERA_NAME_TAKEN" } }]);
    renderAs("SUPERVISOR", <CamerasPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Spare Camera" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Spare Camera" });
    fireEvent.change(within(dialog).getByLabelText(/^Display name/), { target: { value: "Gate Camera" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await within(dialog).findByText("A camera with this display name already exists in this campus.")).toBeTruthy();
  });

  it("removes a camera after confirmation (DELETE /cameras/{id}) and reports a camera with alerts", async () => {
    const calls = mockApi([
      treeRoute,
      listRoute,
      { method: "DELETE", url: /\/cameras\/c2$/, status: 204, body: null },
      { method: "DELETE", url: /\/cameras\/c1$/, status: 409, body: { detail: "This camera has alerts and cannot be removed.", code: "CAMERA_HAS_ALERTS" } },
    ]);
    renderAs("ADMIN", <CamerasPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Spare Camera" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Remove Spare Camera?" })).getByRole("button", { name: "Remove camera" }));
    expect(await screen.findByText("Spare Camera removed.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove Gate Camera" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Remove Gate Camera?" })).getByRole("button", { name: "Remove camera" }));
    expect(await screen.findByText("This camera has alerts and cannot be removed.")).toBeTruthy();
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual(["/api/v1/cameras/c2", "/api/v1/cameras/c1"]);
  });

  it("shows empty and error states", async () => {
    mockApi([treeRoute, { method: "GET", url: /\/v1\/cameras$/, body: [] }]);
    const { unmount } = renderAs("ADMIN", <CamerasPage />);
    expect(await screen.findByText("No cameras yet.")).toBeTruthy();
    unmount();
    mockApi([treeRoute, { method: "GET", url: /\/v1\/cameras$/, status: 500, body: { detail: "Cameras are unavailable." } }]);
    renderAs("ADMIN", <CamerasPage />);
    expect(await screen.findByText("Cameras are unavailable.")).toBeTruthy();
  });
});
