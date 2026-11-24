import type { AppRoute } from "../../app/routeTypes";
import { ReportsPage } from "./pages/ReportsPage";

export const analyticsRoutes: AppRoute[] = [{ path: "/reports", element: <ReportsPage /> }];
