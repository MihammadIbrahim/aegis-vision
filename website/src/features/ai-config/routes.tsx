import type { AppRoute } from "../../app/routeTypes";
import { AiConfigurationPage } from "./pages/AiConfigurationPage";

export const aiConfigRoutes: AppRoute[] = [{ path: "/ai-configuration", element: <AiConfigurationPage /> }];
