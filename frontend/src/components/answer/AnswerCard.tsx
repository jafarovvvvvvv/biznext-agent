import { AlertTriangle, ChevronDown, Code2, Database, HelpCircle, Info, Microscope, OctagonAlert, Sparkles } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useTrace } from "../../api/hooks";
import type { AgentResponse, Highlight } from "../../api/types";
import { formatDuration, formatValue } from "../../lib/format";
import { useI18n } from "../../lib/i18n";
import { ChartView } from "../charts";
import { CopyButton, Delta, ErrorState, Skeleton } from "../ui";
import { cx } from "../../lib/ui-utils";
import { DataTable } from "./DataTable";
import { TraceTimeline } from "./TraceTimeline";

function HighlightRow({ highlight }: { highlight: Highlight }) {
  const tone = {
    positive: "bg-positive",
    negative: "bg-negative",
    warning: "bg-warning",
    neutral: "bg-faint",
  }[highlight.sentiment];
  return (
    <li className="flex gap-2.5 text-sm leading-relaxed text-ink-soft">
      <span className={cx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", tone)} aria-hidden />
      <span>{highlight.text}</span>
    </li>
  );
}

function StatusBanner({ response }: { response: AgentResponse }) {
  const { t } = useI18n();
  const config = {
    insufficient_data: { icon: Info, text: t("agent.statusInsufficient"), style: "bg-accent-soft text-accent-ink" },
    clarification_needed: { icon: HelpCircle, text: t("agent.statusClarification"), style: "bg-accent-soft text-accent-ink" },
    limit_reached: { icon: AlertTriangle, text: t("agent.statusLimit"), style: "bg-warning-soft text-warning" },
    error: { icon: OctagonAlert, text: t("agent.statusError"), style: "bg-negative-soft text-negative" },
    answered: null,
  }[response.status];
  if (!config) return null;
  const Icon = config.icon;
  return (
    <div className={cx("mb-4 inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold", config.style)}>
      <Icon className="h-3.5 w-3.5" aria-hidden /> {config.text}
    </div>
  );
}

function Toggle({ open, onClick, icon: Icon, label }: { open: boolean; onClick: () => void; icon: typeof Code2; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors",
        open ? "border-ink bg-ink text-white" : "border-line bg-surface text-ink-soft hover:border-line-strong hover:text-ink",
      )}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden /> {label}
      <ChevronDown className={cx("h-3.5 w-3.5 transition-transform", open && "rotate-180")} aria-hidden />
    </button>
  );
}

function AnalysisPanel({ traceId }: { traceId: string }) {
  const { t } = useI18n();
  const trace = useTrace(traceId);
  if (trace.isLoading) return <div className="space-y-3"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-6 w-1/2" /><Skeleton className="h-6 w-3/5" /></div>;
  if (trace.error || !trace.data) return <ErrorState error={trace.error} onRetry={() => void trace.refetch()} compact />;
  const data = trace.data;
  const unverified = data.grounding?.unverified ?? [];
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
        <span className="tabular"><b className="text-ink">{data.llm_calls}</b> {t("analysis.llmCalls")}</span>
        <span className="tabular"><b className="text-ink">{data.sql_count}</b> {t("analysis.sql")}</span>
        <span className="tabular"><b className="text-ink">{data.retry_count}</b> {t("analysis.retries")}</span>
      </div>
      <TraceTimeline steps={data.steps} />
      {unverified.length > 0 && (
        <div className="mt-2 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">
          {t("analysis.unverified")}: {unverified.join(", ")}
        </div>
      )}
    </div>
  );
}

export function AnswerCard({ response, onFollowUp }: { response: AgentResponse; onFollowUp?: (question: string) => void }) {
  const { t, lang } = useI18n();
  const [showSql, setShowSql] = useState(false);
  const [showAnalysis, setShowAnalysis] = useState(false);
  const hasSources = response.sources.tables.length > 0 || response.sources.metrics.length > 0;

  return (
    <article className="card overflow-hidden">
      <div className="px-6 pt-6 pb-5 md:px-8 md:pt-7">
        <StatusBanner response={response} />
        {response.summary && (
          <h2 className="font-display text-[21px] font-medium leading-snug tracking-tight text-ink">{response.summary}</h2>
        )}

        {response.metrics.length > 0 && (
          <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {response.metrics.map((metric, index) => (
              <div key={index} className="rounded-xl border border-line bg-paper/60 px-4 py-3">
                <div className="truncate text-xs text-muted" title={metric.label}>{metric.label}</div>
                <div className="tabular mt-1 font-display text-[22px] font-semibold tracking-tight text-ink">
                  {metric.formatted || formatValue(metric.value, metric.unit, lang)}
                </div>
                {metric.change_pct !== null && (
                  <Delta changePct={metric.change_pct} direction={metric.direction} className="mt-1" />
                )}
              </div>
            ))}
          </div>
        )}

        {response.answer && response.answer !== response.summary && (
          <div className="prose-answer mt-5 max-w-3xl text-[15px] leading-relaxed text-ink-soft">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{response.answer}</ReactMarkdown>
          </div>
        )}

        {response.highlights.length > 0 && (
          <ul className="mt-5 space-y-1.5">
            {response.highlights.map((h, index) => (
              <HighlightRow key={index} highlight={h} />
            ))}
          </ul>
        )}

        {response.chart && response.chart.data.length > 0 && (
          <div className="mt-6">
            {response.chart.title && <div className="mb-3 text-sm font-medium text-ink">{response.chart.title}</div>}
            <ChartView spec={response.chart} />
          </div>
        )}

        {response.table && response.table.rows.length > 0 && (
          <div className="mt-6">
            {response.table.title && <div className="mb-3 text-sm font-medium text-ink">{response.table.title}</div>}
            <DataTable table={response.table} />
          </div>
        )}

        {response.limitations && (
          <div className="mt-5 flex gap-2 rounded-lg bg-sunken px-3 py-2.5 text-[13px] text-ink-soft">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted" aria-hidden />
            <span><span className="font-medium text-ink">{t("agent.limitations")}:</span> {response.limitations}</span>
          </div>
        )}
      </div>

      <div className="border-t border-line bg-paper/50 px-6 py-3 md:px-8">
        <div className="flex flex-wrap items-center gap-2">
          {hasSources && (
            <div className="mr-auto flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-muted">
              <Database className="h-3.5 w-3.5" aria-hidden />
              <span>{t("agent.basedOn")}:</span>
              {response.sources.tables.map((table) => (
                <code key={table} className="rounded bg-sunken px-1.5 py-0.5 font-mono text-[11px] text-ink-soft">{table}</code>
              ))}
            </div>
          )}
          <span className="tabular text-xs text-faint">{formatDuration(response.execution_ms, lang)}</span>
          {response.sql.length > 0 && (
            <Toggle open={showSql} onClick={() => setShowSql((v) => !v)} icon={Code2} label={showSql ? t("agent.hideSql") : t("agent.showSql")} />
          )}
          {response.trace_id && (
            <Toggle open={showAnalysis} onClick={() => setShowAnalysis((v) => !v)} icon={Microscope}
                    label={showAnalysis ? t("agent.hideAnalysis") : t("agent.showAnalysis")} />
          )}
        </div>

        {showSql && (
          <div className="mt-3 space-y-3">
            {response.sql.map((entry, index) => (
              <div key={index} className="overflow-hidden rounded-lg border border-line bg-surface">
                <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-xs text-muted">
                  <span className="font-mono text-faint">{entry.result_id}</span>
                  <span className="truncate">{entry.purpose}</span>
                  <span className="tabular ml-auto whitespace-nowrap">{entry.row_count} {t("common.rows")} · {formatDuration(entry.execution_ms, lang)}</span>
                  <CopyButton text={entry.sql} />
                </div>
                <pre className="max-h-64 overflow-auto px-3 py-2 font-mono text-[12px] leading-relaxed text-ink-soft whitespace-pre-wrap">{entry.sql}</pre>
              </div>
            ))}
          </div>
        )}

        {showAnalysis && response.trace_id && (
          <div className="mt-4 rounded-xl border border-line bg-surface p-4">
            <div className="eyebrow mb-3">{t("analysis.title")}</div>
            <AnalysisPanel traceId={response.trace_id} />
          </div>
        )}
      </div>

      {response.follow_up_questions.length > 0 && onFollowUp && (
        <div className="border-t border-line px-6 py-4 md:px-8">
          <div className="mb-2 flex items-center gap-1.5 text-xs text-muted">
            <Sparkles className="h-3.5 w-3.5" aria-hidden /> {t("agent.followUps")}
          </div>
          <div className="flex flex-wrap gap-2">
            {response.follow_up_questions.map((question) => (
              <button
                key={question}
                type="button"
                onClick={() => onFollowUp(question)}
                className="rounded-full border border-line bg-surface px-3 py-1.5 text-left text-[13px] text-ink-soft transition-colors hover:border-accent/40 hover:bg-accent-soft hover:text-accent-ink"
              >
                {question}
              </button>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}
