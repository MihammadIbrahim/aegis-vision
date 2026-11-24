import type { AppRoute } from "../../app/routeTypes";
import { EvidenceDetailPage } from "./pages/EvidenceDetailPage";
import { EvidencePage } from "./pages/EvidencePage";
import { LivePage } from "./pages/LivePage";
import { RecordingsPage } from "./pages/RecordingsPage";

export const videoRoutes: AppRoute[] = [
  { path: "/live", element: <LivePage /> },
  { path: "/recordings", element: <RecordingsPage /> },
  { path: "/evidence", element: <EvidencePage /> },
  { path: "/evidence/:id", element: <EvidenceDetailPage /> },
];
