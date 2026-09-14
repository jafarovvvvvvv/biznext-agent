import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import type { AnswerTable } from "../../api/types";
import { formatCell } from "../../lib/format";
import { useI18n } from "../../lib/i18n";
import { cx } from "../../lib/ui-utils";

export function DataTable({ table, maxHeight = 360 }: { table: AnswerTable; maxHeight?: number }) {
  const { t, lang } = useI18n();
  const [sort, setSort] = useState<{ key: string; desc: boolean } | null>(null);

  const rows = useMemo(() => {
    if (!sort) return table.rows;
    const sorted = [...table.rows].sort((a, b) => {
      const x = a[sort.key];
      const y = b[sort.key];
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      if (typeof x === "number" && typeof y === "number") return x - y;
      return String(x).localeCompare(String(y), lang);
    });
    return sort.desc ? sorted.reverse() : sorted;
  }, [table.rows, sort, lang]);

  const numeric = (type: string) => type === "number" || type === "currency" || type === "percent";

  return (
    <div>
      <div className="overflow-auto rounded-lg border border-line" style={{ maxHeight }}>
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10 bg-sunken">
            <tr>
              {table.columns.map((column) => {
                const active = sort?.key === column.key;
                return (
                  <th key={column.key} scope="col" className={cx("whitespace-nowrap border-b border-line px-3 py-2 font-medium text-muted", numeric(column.type) ? "text-right" : "text-left")}>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 hover:text-ink"
                      onClick={() => setSort((current) => (current?.key === column.key ? { key: column.key, desc: !current.desc } : { key: column.key, desc: numeric(column.type) }))}
                    >
                      {column.label}
                      {active && (sort?.desc ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />)}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-b border-line/70 last:border-0 hover:bg-paper">
                {table.columns.map((column) => {
                  const value = row[column.key];
                  const negative = column.type === "percent" && typeof value === "number" && value < 0;
                  return (
                    <td key={column.key} className={cx("px-3 py-1.5", numeric(column.type) ? "tabular whitespace-nowrap text-right" : "text-ink-soft", negative && "text-negative")}>
                      {formatCell(value, column.type, lang)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(table.truncated || table.total_rows > table.rows.length) && (
        <div className="tabular mt-1.5 text-xs text-muted">
          {table.rows.length} / {table.total_rows} {t("common.rows")}
        </div>
      )}
    </div>
  );
}
