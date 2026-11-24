import type { AppRoute } from "../../app/routeTypes";
import { ReportDetailPage } from "./pages/ReportDetailPage";
import { ReportsPage } from "./pages/ReportsPage";

export const manualReportRoutes: AppRoute[] = [
  { path: "/manual-reports", element: <ReportsPage /> },
  { path: "/manual-reports/:id", element: <ReportDetailPage /> },
];
