import { createBrowserRouter } from "react-router-dom";
import { HealthPage } from "@/pages/HealthPage";

/**
 * Route table. Milestone 1 ships a single route so we can prove the
 * router + query client wiring works. Real pages (Projects, Targets,
 * Scans, Findings, Attack Graph, Reports, ...) are added starting in
 * later milestones, each under `src/pages/` per SRS §9.
 */
export const router = createBrowserRouter([
  {
    path: "/",
    element: <HealthPage />,
  },
]);
