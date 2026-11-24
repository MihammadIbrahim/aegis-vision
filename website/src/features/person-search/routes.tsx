import type { AppRoute } from "../../app/routeTypes";
import { PersonSearchPage } from "./pages/PersonSearchPage";
import { SearchDetailPage } from "./pages/SearchDetailPage";

export const personSearchRoutes: AppRoute[] = [
  { path: "/person-search", element: <PersonSearchPage /> },
  { path: "/person-search/:id", element: <SearchDetailPage /> },
];
