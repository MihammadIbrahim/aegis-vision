import type { AppRoute } from "../../app/routeTypes";
import { NotificationsPage } from "./pages/NotificationsPage";

export const notificationRoutes: AppRoute[] = [{ path: "/notifications", element: <NotificationsPage /> }];
