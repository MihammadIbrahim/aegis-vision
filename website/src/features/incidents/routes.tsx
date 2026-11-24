import type { AppRoute } from "../../app/routeTypes";
import { IncidentDetailPage } from "./pages/IncidentDetailPage";
import { IncidentsPage } from "./pages/IncidentsPage";

export const incidentRoutes: AppRoute[] = [
  { path: "/incidents", element: <IncidentsPage /> },
  { path: "/incidents/:id", element: <IncidentDetailPage /> },
];
