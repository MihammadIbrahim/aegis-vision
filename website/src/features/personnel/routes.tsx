import type { AppRoute } from "../../app/routeTypes";
import { AdminOnly } from "../../auth/AdminOnly";
import { DirectoryPage } from "./pages/DirectoryPage";
import { PersonnelDetailPage } from "./pages/PersonnelDetailPage";
import { PersonnelFormPage } from "./pages/PersonnelFormPage";
import { PersonnelSettingsPage } from "./settings/PersonnelSettingsPage";

export const personnelRoutes: AppRoute[] = [
  { path: "/personnel", element: <DirectoryPage /> },
  { path: "/personnel/new", element: <AdminOnly><PersonnelFormPage /></AdminOnly> },
  { path: "/personnel/settings", element: <AdminOnly><PersonnelSettingsPage /></AdminOnly> },
  { path: "/personnel/:id", element: <PersonnelDetailPage /> },
  { path: "/personnel/:id/edit", element: <AdminOnly><PersonnelFormPage /></AdminOnly> },
];
