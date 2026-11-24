import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import DashboardScreen from "../features/alerts/DashboardScreen";
import NotificationsScreen from "../features/notifications/NotificationsScreen";
import MyReportsScreen from "../features/reports/MyReportsScreen";
import { mockRouter, resetRouter } from "./routerMock";
import { mockApi, ok, secureStore, signedInRoutes, storeSession, withProviders } from "./utils";

jest.mock("expo-router", () => require("./routerMock").routerModule);

const N = (id: string, extra: Record<string, unknown>) => ({
  id,
  kind: "ALERT_NEW",
  title: "Weapon alert at Lobby Cam",
  alert_id: null,
  manual_report_id: null,
  subject_user_id: null,
  camera_id: null,
  incident_id: null,
  created_at: new Date().toISOString(),
  read_at: null,
  ...extra,
});

beforeEach(() => {
  resetRouter();
  secureStore().clear();
  storeSession("STATIONED");
});

describe("Notifications", () => {
  it("shows the unread count on the dashboard bell", async () => {
    mockApi(signedInRoutes({ "GET /notifications": (call) => ok(call.path.includes("unread_only=true") ? [N("n1", {})] : []) }));
    await render(withProviders(<DashboardScreen />));
    expect(await screen.findByLabelText("Notifications, 1 unread")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("open-notifications"));
    expect(mockRouter.push).toHaveBeenCalledWith("/notifications");
  });

  it("opens the alert or case chat a notification is about, marks it read, and marks all read", async () => {
    const api = mockApi(
      signedInRoutes({
        "GET /notifications": ok([
          N("n1", { alert_id: "a1" }),
          N("n2", { kind: "INCIDENT_INVITED", title: "You were added to the chat of INC-000007", incident_id: "i1" }),
        ]),
        "POST /notifications/n1/read": { status: 204 },
        "POST /notifications/read-all": ok({ marked_read: 2 }),
      }),
    );
    await render(withProviders(<NotificationsScreen />));
    await fireEvent.press(await screen.findByTestId("notification-n1"));
    expect(mockRouter.push).toHaveBeenCalledWith("/alert/a1");
    await waitFor(() => expect(api.find("POST", "/notifications/n1/read")).toHaveLength(1));
    await fireEvent.press(screen.getByTestId("notification-n2"));
    expect(mockRouter.push).toHaveBeenCalledWith("/chat/incident/i1");
    await fireEvent.press(screen.getByTestId("read-all"));
    await waitFor(() => expect(api.find("POST", "/notifications/read-all")).toHaveLength(1));
  });
});

describe("Screen 9 — paged history", () => {
  it("loads more submitted reports with limit and offset", async () => {
    const report = (i: number) => ({
      id: `r${i}`, guard_id: "G", guard_name: "Gul A", report_type: `Report ${i}`, description: "d", building_id: "b",
      building_name: "Block", floor_id: null, floor_name: null, area_id: null, area_name: null, has_photo: false,
      occurred_at: null, status: "SUBMITTED", review_note: null, reviewed_at: null, created_at: new Date().toISOString(),
    });
    const api = mockApi(
      signedInRoutes({
        "GET /guard/manual-reports": (call) =>
          ok(call.path.includes("offset=0") ? Array.from({ length: 50 }, (_, i) => report(i)) : [report(50)]),
      }),
    );
    await render(withProviders(<MyReportsScreen />));
    expect(await screen.findByText("Submitted history (50+)")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("load-more-reports"));
    expect(await screen.findByText("Report 50")).toBeTruthy();
    expect(api.calls.filter((c) => c.path.startsWith("/guard/manual-reports")).map((c) => c.path)).toEqual([
      "/guard/manual-reports?limit=50&offset=0",
      "/guard/manual-reports?limit=50&offset=50",
    ]);
  });
});
