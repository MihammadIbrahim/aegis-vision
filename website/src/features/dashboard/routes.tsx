import type { AppRoute } from "../../app/routeTypes";
import { DashboardPage } from "./pages/DashboardPage";

export const dashboardRoutes: AppRoute[] = [{ path: "/dashboard", element: <DashboardPage /> }];
