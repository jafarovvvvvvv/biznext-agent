import { AlertTriangle, ArrowRight, Sparkles, TrendingDown, TrendingUp, Info } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { useActiveSource, useDashboard, useInsights } from "../api/hooks";
import type { Insight, Kpi } from "../api/types";
import { AskBox } from "../components/AskBox";
import { ChartView, Sparkline } from "../components/charts";
import { Delta, ErrorState, PageHeader, Skeleton } from "../components/ui";
import { cx } from "../lib/ui-utils";
import { formatDate, formatValue } from "../lib/format";
import { useI18n } from "../lib/i18n";

function KpiCard({ kpi, compareLabel }: { kpi: Kpi; compareLabel: string }) {
  const { t, lang } = useI18n();
  const tone = kpi.sentiment === "positive" ? "positive" : kpi.sentiment === "negative" ? "negative" : "neutral";
  return (
    <div className="card flex flex-col px-5 pt-4 pb-3">
      <div className="flex items-start justify-between gap-2">
        <span className="text-[13px] text-muted">{kpi.label}</span>
        <Delta changePct={kpi.change_pct} direction={kpi.direction} sentiment={kpi.sentiment} />
      </div>
      <div className="tabular mt-2 font-display text-[27px] font-semibold leading-none tracking-tight text-ink">
        {kpi.formatted || formatValue(kpi.value, kpi.unit, lang)}
      </div>
      <div className="tabular mt-1.5 text-xs text-faint">
        {t("dashboard.vs")} {compareLabel.toLowerCase()}: {formatValue(kpi.compare_value, kpi.unit, lang)}
      </div>
      <div className="mt-auto pt-3">
        <Sparkline points={kpi.sparkline} tone={tone} />
      </div>
    </div>
  );
}

const SEVERITY = {
  negative: { icon: TrendingDown, style: "text-negative bg-negative-soft" },
  positive: { icon: TrendingUp, style: "text-positive bg-positive-soft" },
  warning: { icon: AlertTriangle, style: "text-warning bg-warning-soft" },
  info: { icon: Info, style: "text-accent-ink bg-accent-soft" },
} as const;

function InsightItem({ insight, onAsk }: { insight: Insight; onAsk: (q: string) => void }) {
  const { t } = useI18n();
  const { icon: Icon, style } = SEVERITY[insight.severity] ?? SEVERITY.info;
  return (
    <li className="flex gap-3 py-3.5 first:pt-0 last:pb-0">
      <span className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-lg", style)}>
        <Icon className="h-4 w-4" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium leading-snug text-ink">{insight.title}</div>
        <p className="mt-0.5 text-[13px] leading-relaxed text-muted">{insight.text}</p>
        {insight.suggested_question && (
          <button type="button" onClick={() => onAsk(insight.suggested_question as string)}
                  className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-accent hover:text-accent-ink">
            {t("insights.ask")} <ArrowRight className="h-3 w-3" aria-hidden />
          </button>
        )}
      </div>
    </li>
  );
}

export function DashboardPage() {
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const [period, setPeriod] = useState<string | null>(null);
  const { source } = useActiveSource();
  const dashboard = useDashboard(period);
  const insights = useInsights(dashboard.data?.period.key ?? period);
  const askAgent = (question: string) => navigate("/agent", { state: { question } });
  const data = dashboard.data;

  return (
    <div className="mx-auto max-w-[1360px] px-4 py-8 md:px-8">
      <PageHeader
        eyebrow={data?.period ? `${data.period.label}${data.period.is_partial ? ` · ${t("dashboard.partial")}` : ""}` : undefined}
        title={t("dashboard.title")}
        subtitle={source ? `${t("dashboard.subtitle")} · ${t("dashboard.source")}: ${source.name}` : t("dashboard.subtitle")}
        actions={
          data && (
            <div className="flex flex-wrap gap-1 rounded-xl border border-line bg-surface p-1" role="group">
              {data.available_periods.map((option) => (
                <button key={option.key} type="button" onClick={() => setPeriod(option.key)} aria-pressed={option.key === data.period.key}
                        className={cx("rounded-lg px-3 py-1.5 text-[13px] transition-colors", option.key === data.period.key ? "bg-ink font-medium text-white" : "text-ink-soft hover:bg-sunken")}>
                  {option.label}
                </button>
              ))}
            </div>
          )
        }
      />

      <div className="mb-8 rounded-2xl border border-line bg-gradient-to-br from-surface to-accent-soft/40 p-5 md:p-6">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <div className="font-display text-lg font-semibold tracking-tight">{t("dashboard.askTitle")}</div>
          <div className="text-xs text-muted">{t("dashboard.askHint")}</div>
        </div>
        <AskBox onAsk={askAgent} />
      </div>

      {dashboard.error && !data && <ErrorState error={dashboard.error} onRetry={() => void dashboard.refetch()} />}

      <div className={cx("grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6", dashboard.isFetching && data && "opacity-70 transition-opacity")}>
        {dashboard.isLoading && Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-40 rounded-[14px]" />)}
        {data?.kpis.map((kpi) => <KpiCard key={kpi.metric} kpi={kpi} compareLabel={data.period.compare_label} />)}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          {dashboard.isLoading && <Skeleton className="h-80 rounded-[14px]" />}
          {data?.charts[0] && (
            <div className="card p-5 md:p-6">
              <div className="mb-4 text-sm font-medium text-ink">{data.charts[0].title}</div>
              <ChartView spec={data.charts[0]} height={300} />
            </div>
          )}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {data?.charts.slice(1).map((chart) => (
              <div key={chart.key} className="card p-5 md:p-6">
                <div className="mb-4 text-sm font-medium text-ink">{chart.title}</div>
                <ChartView spec={chart} height={260} />
              </div>
            ))}
          </div>
        </div>

        <div className="card h-fit p-5 md:p-6 xl:sticky xl:top-20">
          <div className="mb-4 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent" aria-hidden />
            <div className="font-display text-base font-semibold tracking-tight">{t("insights.title")}</div>
          </div>
          {insights.isLoading && <div className="space-y-4"><Skeleton className="h-14" /><Skeleton className="h-14" /><Skeleton className="h-14" /></div>}
          {insights.error && <ErrorState error={insights.error} onRetry={() => void insights.refetch()} compact />}
          {insights.data && insights.data.insights.length === 0 && <div className="py-6 text-sm text-muted">{t("insights.empty")}</div>}
          {insights.data && insights.data.insights.length > 0 && (
            <>
              <ul className="divide-y divide-line">
                {insights.data.insights.map((insight) => <InsightItem key={insight.id} insight={insight} onAsk={askAgent} />)}
              </ul>
              <div className="mt-4 border-t border-line pt-3 text-[11px] text-faint">
                {insights.data.source === "llm" ? t("insights.sourceLlm") : t("insights.sourceDeterministic")}
              </div>
            </>
          )}
        </div>
      </div>

      {data?.data_range && (
        <div className="mt-8 text-xs text-faint">
          {t("dashboard.dataRange")}: {formatDate(data.data_range.start, lang)} — {formatDate(data.data_range.end, lang)}
        </div>
      )}
    </div>
  );
}
