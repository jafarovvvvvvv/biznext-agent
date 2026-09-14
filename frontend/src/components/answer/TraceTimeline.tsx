import { AlertCircle, Brain, CheckCircle2, Code2, Database, FileSearch, Flag, RotateCcw, ShieldCheck, Sigma, XCircle } from "lucide-react";
import type { ComponentType } from "react";
import type { TraceStep } from "../../api/types";
import { formatDuration } from "../../lib/format";
import { useI18n } from "../../lib/i18n";
import { CopyButton } from "../ui";
import { cx } from "../../lib/ui-utils";

const ICONS: Record<string, ComponentType<{ className?: string }>> = {
  planning: Brain,
  schema_inspection: FileSearch,
  metric_calculation: Sigma,
  sql_generated: Code2,
  sql_validation: ShieldCheck,
  sql_execution: Database,
  analysis: Brain,
  final_answer: Flag,
  retry: RotateCcw,
  limit_reached: AlertCircle,
  error: XCircle,
};

/** Real agent steps from the stored trace (§13 "Show analysis", §47). */
export function TraceTimeline({ steps, technical = false }: { steps: TraceStep[]; technical?: boolean }) {
  const { t, lang } = useI18n();
  const visible = technical ? steps : steps.filter((s) => s.status !== "started");
  return (
    <ol className="relative space-y-0">
      {visible.map((step, index) => {
        const Icon = ICONS[step.step] ?? CheckCircle2;
        const failed = step.status === "failed";
        const detail = step.detail ?? {};
        const sql = typeof detail.sql === "string" ? detail.sql : Array.isArray(detail.sql) ? (detail.sql as string[]).join(";\n\n") : null;
        const last = index === visible.length - 1;
        return (
          <li key={`${step.seq}-${index}`} className="relative flex gap-3 pb-4">
            {!last && <span className="absolute left-[13px] top-7 bottom-0 w-px bg-line" aria-hidden />}
            <span
              className={cx(
                "relative z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full border",
                failed ? "border-negative/30 bg-negative-soft text-negative" : "border-line bg-surface text-ink-soft",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className={cx("text-sm font-medium", failed ? "text-negative" : "text-ink")}>{step.title}</span>
                {step.duration_ms !== null && step.duration_ms > 0 && (
                  <span className="tabular text-xs text-faint">{formatDuration(step.duration_ms, lang)}</span>
                )}
                {typeof detail.metric === "string" && <span className="font-mono text-xs text-muted">{detail.metric}</span>}
              </div>
              {typeof detail.text === "string" && detail.text && (
                <p className="mt-1 line-clamp-4 text-[13px] leading-relaxed text-ink-soft">{detail.text}</p>
              )}
              {typeof detail.purpose === "string" && detail.purpose && step.step === "sql_generated" && (
                <p className="mt-0.5 text-[13px] text-muted">{detail.purpose}</p>
              )}
              {detail.validation && (
                <p className={cx("mt-0.5 text-[13px]", detail.validation.ok ? "text-positive" : "text-negative")}>
                  {detail.validation.ok ? t("analysis.validationOk") : `${t("analysis.validationFailed")}: ${detail.validation.message ?? ""}`}
                </p>
              )}
              {typeof detail.row_count === "number" && (
                <p className="tabular mt-0.5 text-[13px] text-muted">
                  {detail.row_count} {t("analysis.rows")}
                  {typeof detail.execution_ms === "number" ? ` · ${formatDuration(detail.execution_ms, lang)}` : ""}
                </p>
              )}
              {typeof detail.error === "string" && detail.error && (
                <p className="mt-0.5 text-[13px] text-negative">{detail.error}</p>
              )}
              {sql && (step.step === "sql_generated" || step.step === "metric_calculation" || technical) && (
                <div className="mt-2 overflow-hidden rounded-lg border border-line bg-paper">
                  <div className="flex justify-end border-b border-line px-1">
                    <CopyButton text={sql} />
                  </div>
                  <pre className="max-h-56 overflow-auto px-3 py-2 font-mono text-[12px] leading-relaxed text-ink-soft whitespace-pre-wrap">{sql}</pre>
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
