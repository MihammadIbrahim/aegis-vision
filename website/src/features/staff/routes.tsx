import type { AppRoute } from "../../app/routeTypes";
import { StaffMemberPage } from "./pages/StaffMemberPage";
import { StaffPage } from "./pages/StaffPage";

export const staffRoutes: AppRoute[] = [
  { path: "/staff", element: <StaffPage /> },
  { path: "/staff/:id", element: <StaffMemberPage /> },
];
