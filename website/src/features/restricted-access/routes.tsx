import type { AppRoute } from "../../app/routeTypes";
import { RestrictedAccessPage } from "./pages/RestrictedAccessPage";

export const restrictedAccessRoutes: AppRoute[] = [{ path: "/restricted-access", element: <RestrictedAccessPage /> }];
