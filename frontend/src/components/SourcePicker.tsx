import { ChevronDown, Database, Server } from "lucide-react";
import { useLocation, useNavigate } from "react-router";
import { useActiveSource } from "../api/hooks";
import type { SourceInfo } from "../api/types";
import { useI18n, type MessageKey } from "../lib/i18n";
import { useSettings } from "../lib/settings";
import { cx } from "../lib/ui-utils";

export function SourceIcon({ type, className }: { type: string | undefined; className?: string }) {
  const Icon = type === "1c" ? Server : Database;
  return <Icon className={cx("h-3.5 w-3.5", className)} aria-hidden />;
}

export function SourceTag({ source }: { source: SourceInfo | undefined }) {
  const { t } = useI18n();
  if (!source) return null;
  const key = `source.short.${source.type}` as MessageKey;
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide",
        source.type === "1c" ? "bg-warning-soft text-warning" : "bg-sunken text-muted",
      )}
      title={source.name}
    >
      {t(key) || source.type}
    </span>
  );
}

/** Переключатель источника данных в шапке: дашборд, подсказки и новые диалоги работают с выбранным источником. */
export function SourcePicker() {
  const { t } = useI18n();
  const { setSourceId } = useSettings();
  const { sourceId, source, sources } = useActiveSource();
  const navigate = useNavigate();
  const location = useLocation();
  if (sources.length < 2) return null;

  return (
    <label
      className="relative inline-flex min-w-0 items-center gap-2 rounded-lg border border-line bg-surface py-1 pr-7 pl-2.5 text-xs text-ink hover:border-line-strong"
      title={t("header.source")}
    >
      <SourceIcon type={source?.type} className={source?.type === "1c" ? "text-warning" : "text-accent"} />
      <span className="sr-only">{t("header.source")}</span>
      <select
        value={sourceId}
        onChange={(event) => {
          setSourceId(event.target.value);
          // an open conversation belongs to its source: switching starts a new one
          if (/^\/agent\/.+/.test(location.pathname)) navigate("/agent");
        }}
        className="max-w-[34vw] cursor-pointer appearance-none truncate bg-transparent font-medium outline-none lg:max-w-[18rem]"
      >
        {sources.map((item) => (
          <option key={item.id} value={item.id} disabled={!item.available}>
            {item.name}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-faint" aria-hidden />
    </label>
  );
}
