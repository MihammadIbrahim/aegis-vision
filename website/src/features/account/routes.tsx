import type { AppRoute } from "../../app/routeTypes";
import { AdminOnly } from "../../auth/AdminOnly";
import { AccountPage } from "./pages/AccountPage";
import { OrganizationSettingsPage } from "./pages/OrganizationSettingsPage";
import { ProfilePage } from "./pages/ProfilePage";

export const accountRoutes: AppRoute[] = [
  { path: "/profile", element: <ProfilePage /> },
  { path: "/organization/settings", element: <AdminOnly><OrganizationSettingsPage /></AdminOnly> },
  { path: "/organization/account", element: <AdminOnly><AccountPage /></AdminOnly> },
];
