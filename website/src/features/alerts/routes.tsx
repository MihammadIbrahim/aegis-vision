import type { AppRoute } from "../../app/routeTypes";
import { AlertDetailPage } from "./pages/AlertDetailPage";
import { AlertsPage } from "./pages/AlertsPage";

export const alertRoutes: AppRoute[] = [
  { path: "/alerts", element: <AlertsPage /> },
  { path: "/alerts/:id", element: <AlertDetailPage /> },
];
