import type { AppRoute } from "../../app/routeTypes";
import { AdminOnly } from "../../auth/AdminOnly";
import { UserRecordsPage } from "./pages/UserRecordsPage";

export const userRecordRoutes: AppRoute[] = [{ path: "/user-records", element: <AdminOnly><UserRecordsPage /></AdminOnly> }];
