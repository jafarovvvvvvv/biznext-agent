import { ChevronDown, Database, FileSpreadsheet, Globe, Lock, Server, Sheet, Table2 } from "lucide-react";
import { useState, type ComponentType } from "react";
import { useDataSources, useSourceTables } from "../api/hooks";
import type { DataSource } from "../api/types";
import { ErrorState, PageHeader, Skeleton, StatusDot } from "../components/ui";
import { cx } from "../lib/ui-utils";
import { formatDate, formatDateTime } from "../lib/format";
import { useI18n } from "../lib/i18n";

const ICONS: Record<string, ComponentType<{ className?: string }>> = {
  postgresql: Database,
  "1c": Server,
  excel: FileSpreadsheet,
  google_sheets: Sheet,
  rest_api: Globe,
  mysql: Database,
  mssql: Database,
};

function Detail({ label, value, hint }: { label: string; value: string | number | null | undefined; hint?: string }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular mt-0.5 text-sm font-medium text-ink">{value ?? "—"}</div>
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

function ConnectedSource({ source }: { source: DataSource }) {
  const { t, lang } = useI18n();
  const [showDictionary, setShowDictionary] = useState(true);
  const connected = source.status === "connected";
  const details = source.details;
  return (
    <div className="card p-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="grid h-11 w-11 place-items-center rounded-xl bg-accent-soft text-accent"><Database className="h-5 w-5" aria-hidden /></div>
        <div>
          <div className="font-display text-lg font-semibold tracking-tight">{source.name}</div>
          <div className="text-sm text-muted">{source.description}</div>
        </div>
        <div className={cx("ml-auto inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold", connected ? "bg-positive-soft text-positive" : "bg-negative-soft text-negative")}>
          <StatusDot tone={connected ? "positive" : "negative"} />
          {connected ? t("sources.connected") : t("sources.error")}
        </div>
      </div>
      {details && (
        <div className="mt-6 grid grid-cols-2 gap-5 border-t border-line pt-5 md:grid-cols-3 xl:grid-cols-6">
          <Detail label={t("sources.database")} value={details.database} />
          <Detail label={t("sources.schema")} value={details.schema} />
          <Detail label={t("sources.tables")} value={details.tables} />
          <Detail label={t("sources.rows")} value={details.total_rows?.toLocaleString(lang === "en" ? "en-US" : "ru-RU")} />
          <Detail label={t("sources.period")} value={details.data_start ? `${formatDate(details.data_start, lang)} — ${formatDate(details.data_end, lang)}` : "—"} />
          <Detail label={t("sources.readOnly")} value={details.read_only_user} hint={t("sources.readOnlyHint")} />
        </div>
      )}
      {details?.checked_at && <div className="mt-4 text-[11px] text-faint">{t("sources.checked")}: {formatDateTime(details.checked_at, lang)}</div>}
      {connected && (
        <div className="mt-6">
          <button type="button" onClick={() => setShowDictionary((v) => !v)} className="mb-3 inline-flex items-center gap-1.5 text-sm font-medium text-ink" aria-expanded={showDictionary}>
            {t("sources.dictionary")}
            <ChevronDown className={cx("h-4 w-4 transition-transform", showDictionary && "rotate-180")} aria-hidden />
          </button>
          {showDictionary && <Dictionary sourceId={source.id} />}
        </div>
      )}
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
        {connected.map((source) => <ConnectedSource key={source.id} source={source} />)}
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
