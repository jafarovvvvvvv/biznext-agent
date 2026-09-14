import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router";
import { useTrace } from "../api/hooks";
import { TraceTimeline } from "../components/answer/TraceTimeline";
import { CopyButton, ErrorState, PageHeader, Pill, Skeleton } from "../components/ui";
import { formatDateTime, formatDuration } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { statusTone } from "../lib/ui-utils";

function Stat({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="card px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular mt-1 text-sm font-medium text-ink">{value ?? "—"}</div>
    </div>
  );
}

export function TraceDetailPage() {
  const { t, lang } = useI18n();
  const { traceId } = useParams();
  const trace = useTrace(traceId);
  const data = trace.data;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 md:px-8">
      <Link to="/admin/traces" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
        <ArrowLeft className="h-4 w-4" aria-hidden /> {t("traces.back")}
      </Link>
      {trace.isLoading && <Skeleton className="h-96 rounded-[14px]" />}
      {trace.error && <ErrorState error={trace.error} onRetry={() => void trace.refetch()} />}
      {data && (
        <>
          <PageHeader eyebrow={`trace ${data.trace_id}`} title={data.question}
                      actions={<Pill tone={statusTone(data.status)}>{data.status}</Pill>} />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
            <Stat label={t("traces.when")} value={formatDateTime(data.created_at, lang)} />
            <Stat label={t("traces.time")} value={formatDuration(data.execution_ms, lang)} />
            <Stat label={t("traces.calls")} value={`${data.llm_calls} / ${data.tool_calls} / ${data.sql_count}`} />
            <Stat label={t("traces.retries")} value={data.retry_count} />
            <Stat label={t("traces.model")} value={data.model ? `${data.provider} · ${data.model}` : null} />
            <Stat label={t("traces.usage")} value={data.usage ? `${data.usage.input_tokens ?? "—"} / ${data.usage.output_tokens ?? "—"}` : null} />
          </div>
          {data.error && <div className="mt-4 rounded-lg bg-negative-soft px-4 py-3 text-sm text-negative">{data.error}</div>}
          {(data.grounding?.unverified?.length ?? 0) > 0 && (
            <div className="mt-4 rounded-lg bg-warning-soft px-4 py-3 text-sm text-warning">
              {t("analysis.unverified")}: {data.grounding?.unverified?.join(", ")}
            </div>
          )}
          {(data.grounding?.reference_issues?.length ?? 0) > 0 && (
            <div className="mt-2 rounded-lg bg-warning-soft px-4 py-3 text-xs text-warning">{data.grounding?.reference_issues?.join("; ")}</div>
          )}
          <div className="card mt-6 p-6">
            <TraceTimeline steps={data.steps} technical />
          </div>
          {data.final_response && (
            <div className="card mt-6 overflow-hidden">
              <div className="flex items-center justify-between border-b border-line px-4 py-2 text-sm font-medium">
                {t("traces.finalJson")}
                <CopyButton text={JSON.stringify(data.final_response, null, 2)} />
              </div>
              <pre className="max-h-[480px] overflow-auto px-4 py-3 font-mono text-[12px] leading-relaxed text-ink-soft">
                {JSON.stringify(data.final_response, null, 2)}
              </pre>
            </div>
          )}
        </>
      )}
    </div>
  );
}
