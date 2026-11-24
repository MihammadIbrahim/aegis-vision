import type { AppRoute } from "../../app/routeTypes";
import { CamerasPage } from "./pages/CamerasPage";

export const cameraRoutes: AppRoute[] = [{ path: "/cameras", element: <CamerasPage /> }];
