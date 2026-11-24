import type { AppRoute } from "../../app/routeTypes";
import { AssistantPage } from "./pages/AssistantPage";

// Decision D-027: Org Admin page 19 / Supervisor page 15.
export const assistantRoutes: AppRoute[] = [{ path: "/assistant", element: <AssistantPage /> }];
