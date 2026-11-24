import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import * as ImagePicker from "expo-image-picker";

import MyReportsScreen from "../features/reports/MyReportsScreen";
import ReportScreen, { base64Bytes, locationChoices } from "../features/reports/ReportScreen";
import { resetRouter } from "./routerMock";
import { memoryOutboxStore, mockApi, NETWORK, ok, secureStore, setOnline, signedInRoutes, storeSession, withProviders } from "./utils";

jest.mock("expo-router", () => require("./routerMock").routerModule);

const LOCATIONS = [
  { building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR", floor_id: null, floor_name: null, floor_level: null, area_id: null, area_name: null },
  { building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR", floor_id: "f1", floor_name: "Ground", floor_level: 0, area_id: null, area_name: null },
  { building_id: "b1", building_name: "Admin Block", building_kind: "INDOOR", floor_id: "f1", floor_name: "Ground", floor_level: 0, area_id: "a1", area_name: "Lobby" },
  { building_id: "b2", building_name: "North Fence", building_kind: "OUTDOOR_PERIMETER", floor_id: null, floor_name: null, floor_level: null, area_id: "a2", area_name: "Gate" },
];

beforeEach(() => {
  resetRouter();
  secureStore().clear();
  storeSession("STATIONED");
  setOnline(true);
});

async function choose(testID: string, label: string) {
  await fireEvent.press(await screen.findByTestId(testID));
  await fireEvent.press((await screen.findAllByText(label)).slice(-1)[0]);
}

async function fillReport() {
  await choose("select-type", "Trespassing");
  await choose("select-building", "Admin Block");
  await choose("select-floor", "Ground");
  await choose("select-area", "Lobby");
  await fireEvent.changeText(screen.getByTestId("report-description"), "Person climbing the fence");
}

describe("Screen 8 — Submit Manual Report", () => {
  it("builds building → floor → area choices like the server's location tree", () => {
    const top = locationChoices(LOCATIONS as never, null, null);
    expect(top.buildings.map((b) => b.label)).toEqual(["Admin Block", "North Fence (outdoor perimeter)"]);
    expect(locationChoices(LOCATIONS as never, "b1", "f1").areas).toEqual([{ value: "a1", label: "Lobby" }]);
    expect(locationChoices(LOCATIONS as never, "b2", null).areas).toEqual([{ value: "a2", label: "Gate" }]);
    expect(locationChoices(LOCATIONS as never, "b2", null).floors).toEqual([]);
    expect(base64Bytes("TWFu")).toBe(3);
    expect(base64Bytes("TQ==")).toBe(1);
  });

  it("sends a report with a stable client_report_id and the optional photo", async () => {
    (ImagePicker.launchCameraAsync as jest.Mock).mockResolvedValueOnce({
      canceled: false,
      assets: [{ uri: "file:///photo.jpg", base64: "/9j/4AAQSkZJRg==", mimeType: "image/jpeg" }],
    });
    const api = mockApi(
      signedInRoutes({
        "GET /guard/locations": ok(LOCATIONS),
        "POST /guard/manual-reports": (call) => ok({ report: { id: "r1" }, created: true, echo: call.body }, 201),
      }),
    );
    await render(withProviders(<ReportScreen />));
    await fillReport();
    await fireEvent.press(screen.getByText("Capture photo"));
    await screen.findByLabelText("Attached photo");
    await fireEvent.press(screen.getByTestId("submit-report"));
    expect(await screen.findByText(/Report submitted/)).toBeTruthy();
    expect(api.find("POST", "/guard/manual-reports")[0].body).toEqual({
      client_report_id: expect.any(String),
      report_type: "Trespassing",
      description: "Person climbing the fence",
      building_id: "b1",
      floor_id: "f1",
      area_id: "a1",
      photo_base64: "/9j/4AAQSkZJRg==",
    });
  });

  it("refuses a photo over MANUAL_REPORT_PHOTO_MAX_BYTES", async () => {
    (ImagePicker.launchImageLibraryAsync as jest.Mock).mockResolvedValueOnce({
      canceled: false,
      assets: [{ uri: "file:///big.jpg", base64: "A".repeat(7_000_000), mimeType: "image/jpeg" }],
    });
    mockApi(signedInRoutes({ "GET /guard/locations": ok(LOCATIONS) }));
    await render(withProviders(<ReportScreen />));
    await fireEvent.press(await screen.findByText("From gallery"));
    expect(await screen.findByText(/larger than 5 MB/)).toBeTruthy();
  });

  it("queues the report offline, shows it as Pending offline, and Retry all sends it through /guard/sync", async () => {
    const store = memoryOutboxStore();
    let reachable = false;
    const api = mockApi(
      signedInRoutes({
        "GET /guard/locations": ok(LOCATIONS),
        "POST /guard/manual-reports": () => NETWORK,
        "GET /guard/manual-reports": () => ok([]),
        "POST /guard/sync": () =>
          reachable
            ? ok([{ index: 0, kind: "MANUAL_REPORT", ok: true, http_status: 201, code: null, detail: null, outcome: "CREATED", target_id: "r9" }])
            : NETWORK,
      }),
    );
    const report = await render(withProviders(<ReportScreen />, { store }));
    await fillReport();
    await fireEvent.press(screen.getByTestId("submit-report"));
    expect(await screen.findByText(/saved on this phone and will be sent automatically/)).toBeTruthy();
    expect(store.items).toHaveLength(1);
    const queuedId = store.items[0].client_report_id;
    expect(store.items[0].owner_guard_id).toBe("ABCDEF123456789012345678901");
    await report.unmount();

    await render(withProviders(<MyReportsScreen />, { store }));
    expect(await screen.findByText("1 report in the offline outbox")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("state-PENDING")).toBeTruthy());
    reachable = true;
    await fireEvent.press(screen.getByTestId("retry-all"));
    await waitFor(() => expect(store.items).toHaveLength(0));
    const synced = api.find("POST", "/guard/sync").slice(-1)[0].body as { items: { kind: string; report: { client_report_id: string } }[] };
    expect(synced.items[0]).toMatchObject({ kind: "MANUAL_REPORT", report: { client_report_id: queuedId } });
  });
});

describe("Screen 9 — My Reports", () => {
  it("shows the server's history as Synced with the review state, and Failed items with the reason", async () => {
    const store = memoryOutboxStore([
      {
        client_report_id: "f1",
        owner_guard_id: "ABCDEF123456789012345678901",
        report: { client_report_id: "f1", report_type: "Lost Item", description: "Bag", building_id: "gone" },
        photo_file: null,
        state: "FAILED",
        attempts: 1,
        last_error: "Unknown building.",
        queued_at: "2026-11-19T10:00:00Z",
        display: { building_name: "Old block", floor_name: null, area_name: null },
      },
    ]);
    mockApi(
      signedInRoutes({
        "GET /guard/manual-reports": ok([
          {
            id: "r1", guard_id: "G", guard_name: "Gul A", report_type: "Trespassing", description: "Fence", building_id: "b2",
            building_name: "North Fence", floor_id: null, floor_name: null, area_id: "a2", area_name: "Gate", has_photo: true,
            occurred_at: null, status: "REVIEWED", review_note: "Patrol added", reviewed_at: "2026-11-19T11:00:00Z", created_at: "2026-11-19T10:00:00Z",
          },
        ]),
      }),
    );
    await render(withProviders(<MyReportsScreen />, { store }));
    expect(await screen.findByText("Synced")).toBeTruthy();
    expect(screen.getByText("Reviewed")).toBeTruthy();
    expect(screen.getByText("Review note: Patrol added")).toBeTruthy();
    expect(screen.getByTestId("state-FAILED")).toBeTruthy();
    expect(screen.getByText("Unknown building.")).toBeTruthy();
  });
});
