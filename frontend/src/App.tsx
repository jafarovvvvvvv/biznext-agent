import { Navigate, Route, Routes } from "react-router";
import { AppShell } from "./components/layout/AppShell";
import { AgentPage } from "./pages/AgentPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DataSourcesPage } from "./pages/DataSourcesPage";
import { ReportsPage } from "./pages/ReportsPage";
import { TraceDetailPage } from "./pages/TraceDetailPage";
import { TracesPage } from "./pages/TracesPage";

export function App() {
  return (
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
  );
}
