import { accountRoutes } from "../features/account/routes";
import { aiConfigRoutes } from "../features/ai-config/routes";
import { analyticsRoutes } from "../features/analytics/routes";
import { assistantRoutes } from "../features/assistant/routes";
import { auditLogRoutes } from "../features/audit-logs/routes";
import { campusRequestRoutes } from "../features/campus-requests/routes";
import { dashboardRoutes } from "../features/dashboard/routes";
import { incidentRoutes } from "../features/incidents/routes";
import { personSearchRoutes } from "../features/person-search/routes";
import { userRecordRoutes } from "../features/user-records/routes";
import { alertRoutes } from "../features/alerts/routes";
import { cameraRoutes } from "../features/cameras/routes";
import { locationRoutes } from "../features/locations/routes";
import { manualReportRoutes } from "../features/manual-reports/routes";
import { notificationRoutes } from "../features/notifications/routes";
import { personnelRoutes } from "../features/personnel/routes";
import { personnelImportRoutes } from "../features/personnel-import/routes";
import { restrictedAccessRoutes } from "../features/restricted-access/routes";
import { staffRoutes } from "../features/staff/routes";
import { videoRoutes } from "../features/video/routes";
import type { AppRoute } from "./routeTypes";

export type { AppRoute };

// Each feature owns its route list; a new feature adds one line here.
export const APP_ROUTES: AppRoute[] = [
  ...dashboardRoutes,
  ...alertRoutes,
  ...manualReportRoutes,
  ...notificationRoutes,
  ...locationRoutes,
  ...cameraRoutes,
  ...videoRoutes,
  ...staffRoutes,
  ...personnelRoutes,
  ...personnelImportRoutes,
  ...restrictedAccessRoutes,
  // Decision D-024.
  ...incidentRoutes,
  ...personSearchRoutes,
  ...aiConfigRoutes,
  ...userRecordRoutes,
  ...analyticsRoutes,
  ...auditLogRoutes,
  // Decision D-027.
  ...assistantRoutes,
  ...campusRequestRoutes,
  ...accountRoutes,
];
