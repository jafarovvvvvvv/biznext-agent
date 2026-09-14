import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";
import { AppShell } from "./components/layout/AppShell";
import { AgentPage } from "./pages/AgentPage";
import { DashboardPage } from "./pages/DashboardPage";

const ReportsPage = lazy(() => import("./pages/ReportsPage").then((m) => ({ default: m.ReportsPage })));
const DataSourcesPage = lazy(() => import("./pages/DataSourcesPage").then((m) => ({ default: m.DataSourcesPage })));
const TracesPage = lazy(() => import("./pages/TracesPage").then((m) => ({ default: m.TracesPage })));
const TraceDetailPage = lazy(() => import("./pages/TraceDetailPage").then((m) => ({ default: m.TraceDetailPage })));

export function App() {
  return (
    <Suspense fallback={null}>
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<DashboardPage />} />
        <Route path="agent/:conversationId?" element={<AgentPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="data-sources" element={<DataSourcesPage />} />
        <Route path="admin/traces" element={<TracesPage />} />
        <Route path="admin/traces/:traceId" element={<TraceDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </Suspense>
  );
}
