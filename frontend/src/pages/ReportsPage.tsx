import { BarChart3, FileText, Table2 } from "lucide-react";
import { useNavigate } from "react-router";
import { useReports } from "../api/hooks";
import type { AgentStatus } from "../api/types";
import { EmptyState, ErrorState, PageHeader, Pill, Skeleton } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { useI18n, type MessageKey } from "../lib/i18n";

const STATUS_KEYS: Partial<Record<AgentStatus, MessageKey>> = {
  insufficient_data: "agent.statusInsufficient",
  clarification_needed: "agent.statusClarification",
  limit_reached: "agent.statusLimit",
  error: "agent.statusError",
};

export function ReportsPage() {
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const reports = useReports();

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 md:px-8">
      <PageHeader title={t("reports.title")} subtitle={t("reports.subtitle")} />
      {reports.isLoading && <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 rounded-[14px]" />)}</div>}
      {reports.error && <ErrorState error={reports.error} onRetry={() => void reports.refetch()} />}
      {reports.data && reports.data.reports.length === 0 && (
        <EmptyState icon={<FileText className="h-8 w-8" />} title={t("reports.empty")} />
      )}
      <div className="space-y-3">
        {reports.data?.reports.map((report) => {
          const statusKey = STATUS_KEYS[report.status];
          return (
            <button
              key={report.message_id}
              type="button"
              onClick={() => navigate(`/agent/${report.conversation_id}`)}
              className="card block w-full px-5 py-4 text-left transition-shadow hover:shadow-[var(--shadow-float)]"
            >
              <div className="flex items-center gap-2 text-xs text-muted">
                <span className="tabular">{formatDateTime(report.created_at, lang)}</span>
                {statusKey && <Pill tone={report.status === "error" ? "negative" : "warning"}>{t(statusKey)}</Pill>}
                <span className="ml-auto flex gap-1.5 text-faint">
                  {report.has_chart && <BarChart3 className="h-4 w-4" aria-hidden />}
                  {report.has_table && <Table2 className="h-4 w-4" aria-hidden />}
                </span>
              </div>
              <div className="mt-1.5 text-[15px] font-medium text-ink">{report.question}</div>
              {report.summary && <div className="mt-1 line-clamp-2 text-sm text-muted">{report.summary}</div>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
