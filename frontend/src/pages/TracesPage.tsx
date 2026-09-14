import { Activity } from "lucide-react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import { useTraces } from "../api/hooks";
import { EmptyState, ErrorState, PageHeader, Pill, Skeleton } from "../components/ui";
import { formatDateTime, formatDuration } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { statusTone } from "../lib/ui-utils";

export function TracesPage() {
  const { t, lang } = useI18n();
  const traces = useTraces();

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 md:px-8">
      <PageHeader eyebrow={t("nav.developer")} title={t("traces.title")} subtitle={t("traces.subtitle")} />
      {traces.isLoading && <Skeleton className="h-64 rounded-[14px]" />}
      {traces.error instanceof ApiError && traces.error.code === "unauthorized" ? (
        <EmptyState icon={<Activity className="h-8 w-8" />} title={t("traces.unauthorized")} />
      ) : (
        traces.error && <ErrorState error={traces.error} onRetry={() => void traces.refetch()} />
      )}
      {traces.data && traces.data.traces.length === 0 && <EmptyState icon={<Activity className="h-8 w-8" />} title={t("traces.empty")} />}
      {traces.data && traces.data.traces.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead className="bg-sunken text-left text-xs text-muted">
              <tr>
                <th className="px-4 py-2.5 font-medium">{t("traces.when")}</th>
                <th className="px-4 py-2.5 font-medium">{t("traces.question")}</th>
                <th className="px-4 py-2.5 font-medium">{t("traces.status")}</th>
                <th className="px-4 py-2.5 text-right font-medium">{t("traces.time")}</th>
                <th className="px-4 py-2.5 text-right font-medium">{t("traces.calls")}</th>
                <th className="px-4 py-2.5 text-right font-medium">{t("traces.retries")}</th>
              </tr>
            </thead>
            <tbody>
              {traces.data.traces.map((trace) => (
                <tr key={trace.trace_id} className="border-t border-line hover:bg-paper">
                  <td className="tabular px-4 py-2.5 whitespace-nowrap text-muted">{formatDateTime(trace.created_at, lang)}</td>
                  <td className="max-w-md px-4 py-2.5">
                    <Link to={`/admin/traces/${trace.trace_id}`} className="line-clamp-2 text-ink hover:text-accent">{trace.question}</Link>
                  </td>
                  <td className="px-4 py-2.5"><Pill tone={statusTone(trace.status)}>{trace.status}</Pill></td>
                  <td className="tabular px-4 py-2.5 text-right whitespace-nowrap">{formatDuration(trace.execution_ms, lang)}</td>
                  <td className="tabular px-4 py-2.5 text-right whitespace-nowrap">{trace.llm_calls} / {trace.tool_calls} / {trace.sql_count}</td>
                  <td className="tabular px-4 py-2.5 text-right">{trace.retry_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
