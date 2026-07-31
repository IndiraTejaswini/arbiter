import React from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import AuditPage from "./routes/AuditPage";
import CaseDetailPage from "./routes/CaseDetailPage";
import CasesPage from "./routes/CasesPage";
import FairnessPage from "./routes/FairnessPage";
import FilePage from "./routes/FilePage";
import NotFoundPage from "./routes/NotFoundPage";
import OperationsPage from "./routes/OperationsPage";
import OverviewPage from "./routes/OverviewPage";
import ProvenancePage from "./routes/ProvenancePage";
import ReviewPage from "./routes/ReviewPage";
import RulepacksPage from "./routes/RulepacksPage";
import "./styles.css";

/**
 * Pure React SPA on Vite. No Next.js, no SSR, no server runtime.
 *
 * Every route renders exclusively from authenticated API responses, so
 * there is nothing for a server framework to pre-render — a static bundle
 * plus the FastAPI service is the honest shape for this application.
 */
const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    errorElement: <ErrorBoundary />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "cases", element: <CasesPage /> },
      { path: "cases/:caseId", element: <CaseDetailPage /> },
      { path: "cases/:caseId/review", element: <ReviewPage /> },
      { path: "cases/:caseId/audit", element: <AuditPage /> },
      { path: "file", element: <FilePage /> },
      { path: "fairness", element: <FairnessPage /> },
      { path: "rulepacks", element: <RulepacksPage /> },
      { path: "provenance", element: <ProvenancePage /> },
      { path: "operations", element: <OperationsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
