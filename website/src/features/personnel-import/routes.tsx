import type { AppRoute } from "../../app/routeTypes";
import { AdminOnly } from "../../auth/AdminOnly";
import { ImportHistoryPage } from "./pages/ImportHistoryPage";
import { ImportWizardPage } from "./pages/ImportWizardPage";

export const personnelImportRoutes: AppRoute[] = [
  { path: "/personnel/imports", element: <AdminOnly><ImportHistoryPage /></AdminOnly> },
  { path: "/personnel/imports/new", element: <AdminOnly><ImportWizardPage /></AdminOnly> },
  { path: "/personnel/imports/:id", element: <AdminOnly><ImportWizardPage /></AdminOnly> },
];
