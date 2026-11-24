import type { AppRoute } from "../../app/routeTypes";
import { AuditLogsPage } from "./pages/AuditLogsPage";

export const auditLogRoutes: AppRoute[] = [{ path: "/audit-logs", element: <AuditLogsPage /> }];
