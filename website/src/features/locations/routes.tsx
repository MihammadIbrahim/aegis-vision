import type { AppRoute } from "../../app/routeTypes";
import { LocationsPage } from "./pages/LocationsPage";

export const locationRoutes: AppRoute[] = [{ path: "/locations", element: <LocationsPage /> }];
