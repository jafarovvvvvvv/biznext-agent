import {
  ArrowRight,
  Bot,
  Check,
  ChevronDown,
  Database,
  FileSpreadsheet,
  Globe,
  Lock,
  Server,
  ShieldCheck,
  Sheet,
  Table2,
  Workflow,
} from "lucide-react";
import { useState, type ComponentType, type ReactNode } from "react";
import { useActiveSource, useDataSources, useSourceTables } from "../api/hooks";
import type { DataSource, SyncRun, SyncStatus } from "../api/types";
import { ErrorState, PageHeader, Pill, Skeleton, StatusDot } from "../components/ui";
import { cx } from "../lib/ui-utils";
import { formatDate, formatDateTime } from "../lib/format";
import { useI18n, type MessageKey } from "../lib/i18n";
import { useSettings } from "../lib/settings";

const ICONS: Record<string, ComponentType<{ className?: string }>> = {
  postgresql: Database,
  "1c": Server,
  excel: FileSpreadsheet,
  google_sheets: Sheet,
  rest_api: Globe,
  mysql: Database,
  mssql: Database,
};

function Detail({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular mt-0.5 truncate text-sm font-medium text-ink">{value ?? "—"}</div>
      {hint && <div className="text-[11px] text-faint">{hint}</div>}
    </div>
  );
}

function Dictionary({ sourceId }: { sourceId: string }) {
  const { t } = useI18n();
  const tables = useSourceTables(sourceId);
  const [open, setOpen] = useState<string | null>(null);
  if (tables.isLoading) return <div className="space-y-2"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>;
  if (tables.error || !tables.data) return <ErrorState error={tables.error} compact />;
  return (
    <ul className="divide-y divide-line rounded-xl border border-line">
      {tables.data.tables.map((table) => (
        <li key={table.name}>
          <button type="button" onClick={() => setOpen(open === table.name ? null : table.name)} aria-expanded={open === table.name}
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-paper">
            <Table2 className="h-4 w-4 shrink-0 text-faint" aria-hidden />
            <code className="font-mono text-[13px] text-ink">{table.name}</code>
            <span className="hidden truncate text-[13px] text-muted sm:block">{table.description}</span>
            <span className="tabular ml-auto whitespace-nowrap text-xs text-faint">
              {table.row_count?.toLocaleString() ?? "—"} · {table.columns.length} {t("sources.columns")}
            </span>
            <ChevronDown className={cx("h-4 w-4 shrink-0 text-faint transition-transform", open === table.name && "rotate-180")} aria-hidden />
          </button>
          {open === table.name && (
            <div className="overflow-x-auto bg-paper px-4 pb-3">
              <table className="w-full text-[13px]">
                <tbody>
                  {table.columns.map((column) => (
                    <tr key={column.name} className="border-t border-line/70 first:border-0">
                      <td className="py-1.5 pr-4 font-mono text-[12px] text-ink">{column.name}</td>
                      <td className="py-1.5 pr-4 font-mono text-[12px] whitespace-nowrap text-faint">{column.type}</td>
                      <td className="py-1.5 text-ink-soft">{column.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

function SourceHeader({ source }: { source: DataSource }) {
  const { t } = useI18n();
  const { setSourceId } = useSettings();
  const active = useActiveSource();
  const Icon = ICONS[source.type] ?? Database;
  const tone = source.status === "connected" ? "positive" : source.status === "waiting_for_sync" ? "warning" : "negative";
  const statusText = source.status === "connected" ? t("sources.connected") : source.status === "waiting_for_sync" ? t("sources.waiting") : t("sources.error");
  const isActive = active.sourceId === source.id;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className={cx("grid h-11 w-11 place-items-center rounded-xl", source.type === "1c" ? "bg-warning-soft text-warning" : "bg-accent-soft text-accent")}>
        <Icon className="h-5 w-5" aria-hidden />
      </div>
      <div className="min-w-[min(100%,18rem)] flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-display text-lg font-semibold tracking-tight">{source.name}</span>
          {source.mode === "demo" && source.storage === "canonical" && <Pill tone="warning">{t("sources.demoBadge")}</Pill>}
        </div>
        <div className="max-w-3xl text-sm text-muted">{source.description}</div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {isActive ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft px-3 py-1 text-xs font-medium text-accent-ink">
            <Check className="h-3.5 w-3.5" aria-hidden /> {t("sources.active")}
          </span>
        ) : (
          <button type="button" onClick={() => setSourceId(source.id)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-ink hover:border-line-strong">
            {t("sources.useSource")} <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </button>
        )}
        <div className={cx("inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold",
                           tone === "positive" ? "bg-positive-soft text-positive" : tone === "warning" ? "bg-warning-soft text-warning" : "bg-negative-soft text-negative")}>
          <StatusDot tone={tone === "warning" ? "accent" : tone} />
          {statusText}
        </div>
      </div>
    </div>
  );
}

function DatabaseDetails({ source }: { source: DataSource }) {
  const { t, lang } = useI18n();
  const details = source.details;
  if (!details) return null;
  return (
    <>
      <div className="mt-6 grid grid-cols-2 gap-5 border-t border-line pt-5 md:grid-cols-3 xl:grid-cols-6">
        <Detail label={t("sources.database")} value={details.database} />
        <Detail label={t("sources.schema")} value={details.schema} />
        <Detail label={t("sources.tables")} value={details.tables} />
        <Detail label={t("sources.rows")} value={details.total_rows?.toLocaleString(lang === "en" ? "en-US" : "ru-RU")} />
        <Detail label={t("sources.period")} value={details.data_start ? `${formatDate(details.data_start, lang)} — ${formatDate(details.data_end, lang)}` : "—"} />
        <Detail label={t("sources.readOnly")} value={details.read_only_user} hint={t("sources.readOnlyHint")} />
      </div>
      {details.checked_at && <div className="mt-4 text-[11px] text-faint">{t("sources.checked")}: {formatDateTime(details.checked_at, lang)}</div>}
    </>
  );
}

function DictionaryToggle({ source, initiallyOpen }: { source: DataSource; initiallyOpen: boolean }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(initiallyOpen);
  if (source.status === "error") return null;
  return (
    <div className="mt-6">
      <button type="button" onClick={() => setOpen((v) => !v)} className="mb-3 inline-flex items-center gap-1.5 text-sm font-medium text-ink" aria-expanded={open}>
        {t("sources.dictionary")}
        <ChevronDown className={cx("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && <Dictionary sourceId={source.id} />}
    </div>
  );
}

function ConnectedSource({ source }: { source: DataSource }) {
  return (
    <div className="card p-6">
      <SourceHeader source={source} />
      <DatabaseDetails source={source} />
      <DictionaryToggle source={source} initiallyOpen={false} />
    </div>
  );
}

function FlowNode({ icon: Icon, title, hint, tone }: { icon: ComponentType<{ className?: string }>; title: string; hint: string; tone: "warning" | "accent" | "ink" }) {
  const styles = { warning: "bg-warning-soft text-warning", accent: "bg-accent-soft text-accent", ink: "bg-ink text-white" };
  return (
    <div className="flex min-w-0 flex-1 items-start gap-3 rounded-xl border border-line bg-surface px-4 py-3 shadow-[var(--shadow-card)]">
      <span className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-lg", styles[tone])}><Icon className="h-4.5 w-4.5" aria-hidden /></span>
      <div className="min-w-0">
        <div className="text-sm font-semibold text-ink">{title}</div>
        <div className="text-xs leading-snug text-muted">{hint}</div>
      </div>
    </div>
  );
}

function Arrow({ label }: { label?: string }) {
  return (
    <div className="flex shrink-0 flex-col items-center justify-center gap-1 px-1 py-1 text-faint lg:py-0">
      <ArrowRight className="h-4 w-4 rotate-90 lg:rotate-0" aria-hidden />
      {label && <span className="max-w-28 text-center text-[10px] leading-tight font-medium text-muted">{label}</span>}
    </div>
  );
}

function DataFlow() {
  const { t } = useI18n();
  return (
    <div className="mt-6 rounded-2xl border border-line bg-paper p-4 md:p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-ink">
        <Workflow className="h-4 w-4 text-accent" aria-hidden /> {t("sources.flowTitle")}
      </div>
      <div className="flex flex-col items-stretch lg:flex-row lg:items-center">
        <div className="flex flex-1 flex-col rounded-xl border-2 border-dashed border-warning/40 p-3 lg:flex-row lg:items-center">
          <div className="eyebrow mb-2 text-warning lg:hidden">{t("sources.flowEnterprise")}</div>
          <FlowNode icon={Server} title={t("sources.flow1c")} hint={t("sources.flow1cHint")} tone="warning" />
          <Arrow label="OData · GET" />
          <FlowNode icon={ShieldCheck} title={t("sources.flowConnector")} hint={t("sources.flowConnectorHint")} tone="accent" />
        </div>
        <Arrow label={t("sources.flowChannel")} />
        <FlowNode icon={Bot} title={t("sources.flowBiznext")} hint={t("sources.flowBiznextHint")} tone="ink" />
      </div>
      <div className="mt-3 hidden text-[11px] font-medium text-warning lg:block">{t("sources.flowEnterprise")}</div>
      <p className="mt-2 text-xs leading-relaxed text-muted">{t("sources.flowNote")}</p>
    </div>
  );
}

function RunRow({ run }: { run: SyncRun }) {
  const { t, lang } = useI18n();
  const tone = run.status === "succeeded" ? "positive" : run.status === "failed" ? "negative" : "accent";
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-[13px]">
      <StatusDot tone={tone} />
      <span className="tabular text-ink">{formatDateTime(run.finished_at ?? run.started_at, lang)}</span>
      <span className="text-muted">{t(`sources.kind.${run.kind}` as MessageKey)}</span>
      <span className="text-muted">{t(`sources.run.${run.status}` as MessageKey)}</span>
      {run.duration_s !== null && <span className="tabular text-faint">{run.duration_s} {t("agent.seconds")}</span>}
      {run.error && <span className="w-full truncate text-xs text-negative" title={run.error}>{run.error}</span>}
    </li>
  );
}

function SyncPanel({ sync }: { sync: SyncStatus }) {
  const { t, lang } = useI18n();
  const locale = lang === "en" ? "en-US" : "ru-RU";
  const lastRun = sync.runs.find((run) => run.status !== "running") ?? sync.runs[0];
  const security: MessageKey[] = ["sources.sec.noInbound", "sources.sec.readOnly", "sources.sec.minimal", "sources.sec.contract", "sources.sec.agent"];
  return (
    <>
      <div className="mt-6 grid grid-cols-2 gap-5 border-t border-line pt-5 md:grid-cols-3 xl:grid-cols-5">
        <Detail label={t("sources.lastSync")} value={lastRun ? formatDateTime(lastRun.finished_at ?? lastRun.started_at, lang) : "—"}
                hint={lastRun ? `${t(`sources.kind.${lastRun.kind}` as MessageKey)} · ${t(`sources.run.${lastRun.status}` as MessageKey)}` : undefined} />
        <Detail label={t("sources.configuration")} value={<span title={sync.connector.configuration ?? ""}>{sync.connector.configuration ?? "—"}</span>} />
        <Detail label={t("sources.connectorVersion")} value={sync.connector.version ? `v${sync.connector.version}` : "—"}
                hint={sync.connector.adapter ?? undefined} />
        <Detail label={t("sources.contract")} value={`canonical v${sync.contract_version}`} />
        <Detail label={t("sources.lastSeen")} value={sync.connector.last_seen_at ? formatDateTime(sync.connector.last_seen_at, lang) : "—"} />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <div className="mb-3 text-sm font-medium text-ink">{t("sources.entities")}</div>
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full text-[13px]">
              <thead className="bg-paper text-left text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">{t("sources.entity")}</th>
                  <th className="px-3 py-2 font-medium">{t("sources.objects")}</th>
                  <th className="px-3 py-2 text-right font-medium">{t("sources.records")}</th>
                  <th className="px-3 py-2 font-medium">{t("sources.updated")}</th>
                </tr>
              </thead>
              <tbody>
                {sync.entities.map((entity) => (
                  <tr key={entity.entity} className="border-t border-line align-top">
                    <td className="px-3 py-2">
                      <div className="font-medium text-ink">{(lang === "en" ? entity.label.en : entity.label.ru) ?? entity.entity}</div>
                      <code className="font-mono text-[11px] text-faint">{entity.entity}</code>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        {entity.source_objects.slice(0, 3).map((obj) => (
                          <code key={obj} className="rounded bg-sunken px-1.5 py-px font-mono text-[11px] text-ink-soft">{obj}</code>
                        ))}
                        {entity.source_objects.length > 3 && <span className="text-[11px] text-faint">+{entity.source_objects.length - 3}</span>}
                      </div>
                      {entity.excluded_fields.length > 0 && (
                        <div className="mt-1 flex items-center gap-1 text-[11px] text-muted">
                          <Lock className="h-3 w-3" aria-hidden /> {t("sources.notTransferred")}: {entity.excluded_fields.join(", ")}
                        </div>
                      )}
                    </td>
                    <td className="tabular px-3 py-2 text-right text-ink">{entity.rows.toLocaleString(locale)}</td>
                    <td className="tabular px-3 py-2 whitespace-nowrap text-muted">{entity.last_loaded_at ? formatDateTime(entity.last_loaded_at, lang) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="space-y-6">
          <div>
            <div className="mb-3 text-sm font-medium text-ink">{t("sources.security")}</div>
            <ul className="space-y-2.5">
              {security.map((key) => (
                <li key={key} className="flex gap-2.5 text-[13px] leading-snug text-ink-soft">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-positive" aria-hidden />
                  {t(key)}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="mb-2 text-sm font-medium text-ink">{t("sources.runs")}</div>
            {sync.runs.length === 0 ? (
              <div className="text-[13px] text-muted">{t("sources.noRuns")}</div>
            ) : (
              <ul className="divide-y divide-line">{sync.runs.map((run) => <RunRow key={run.run_id} run={run} />)}</ul>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function CanonicalSource({ source }: { source: DataSource }) {
  return (
    <div className="card p-6">
      <SourceHeader source={source} />
      <DataFlow />
      {source.sync ? <SyncPanel sync={source.sync} /> : <DatabaseDetails source={source} />}
      <DictionaryToggle source={source} initiallyOpen={false} />
    </div>
  );
}

export function DataSourcesPage() {
  const { t } = useI18n();
  const sources = useDataSources();
  const connected = sources.data?.sources.filter((s) => s.status !== "coming_soon") ?? [];
  const future = sources.data?.sources.filter((s) => s.status === "coming_soon") ?? [];

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 md:px-8">
      <PageHeader title={t("sources.title")} subtitle={t("sources.subtitle")} />
      {sources.isLoading && <Skeleton className="h-64 rounded-[14px]" />}
      {sources.error && <ErrorState error={sources.error} onRetry={() => void sources.refetch()} />}
      <div className="space-y-6">
        {connected.map((source) =>
          source.storage === "canonical" ? <CanonicalSource key={source.id} source={source} /> : <ConnectedSource key={source.id} source={source} />,
        )}
      </div>
      {future.length > 0 && (
        <>
          <div className="eyebrow mt-10 mb-3">{t("sources.future")}</div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {future.map((source) => {
              const Icon = ICONS[source.type] ?? Database;
              return (
                <div key={source.id} className="flex items-center gap-3 rounded-[14px] border border-dashed border-line-strong bg-paper px-4 py-4 opacity-80" aria-disabled="true">
                  <div className="grid h-9 w-9 place-items-center rounded-lg bg-sunken text-faint"><Icon className="h-4.5 w-4.5" aria-hidden /></div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-ink-soft">{source.name}</div>
                    <div className="text-xs text-faint">{t("sources.notConnected")}</div>
                  </div>
                  <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-sunken px-2 py-0.5 text-[11px] font-medium text-muted">
                    <Lock className="h-3 w-3" aria-hidden /> {t("sources.comingSoon")}
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
