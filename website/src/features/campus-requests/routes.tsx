import type { AppRoute } from "../../app/routeTypes";
import { AdminOnly } from "../../auth/AdminOnly";
import { CampusRequestsPage } from "./pages/CampusRequestsPage";

export const campusRequestRoutes: AppRoute[] = [
  { path: "/campus-requests", element: <AdminOnly><CampusRequestsPage /></AdminOnly> },
];
