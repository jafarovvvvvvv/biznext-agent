import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import { useId } from "react";
import type { ChartSeries, ChartSpec, DataRow, SparklinePoint, Unit } from "../api/types";
import { formatAxis, formatMonth, formatValue } from "../lib/format";
import { useI18n } from "../lib/i18n";

const CHART_COLORS = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
  "var(--color-chart-6)",
  "var(--color-chart-7)",
  "var(--color-chart-8)",
];

const AXIS_TICK = { fill: "var(--color-muted)", fontSize: 11 };

function useXFormatter(spec: Pick<ChartSpec, "x_type" | "data" | "x_key">) {
  const { lang } = useI18n();
  return (value: unknown): string => {
    if (value === null || value === undefined) return "";
    const text = String(value);
    if (spec.x_type === "date" && /^\d{4}-\d{2}-\d{2}/.test(text)) {
      const dates = spec.data.map((row) => String(row[spec.x_key] ?? ""));
      const monthly = dates.every((d) => d.slice(8, 10) === "01");
      if (monthly) return formatMonth(text.slice(0, 10), lang, true);
      return new Date(`${text.slice(0, 10)}T00:00:00`).toLocaleDateString(lang === "en" ? "en-US" : "ru-RU", {
        day: "numeric",
        month: "short",
      });
    }
    return text.length > 18 ? `${text.slice(0, 17)}…` : text;
  };
}

function ChartTooltip({ active, payload, label, series, xFormat }: TooltipContentProps<number, string> & {
  series: ChartSeries[];
  xFormat: (value: unknown) => string;
}) {
  const { lang } = useI18n();
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-[var(--shadow-float)]">
      <div className="mb-1 font-medium text-ink">{xFormat(label ?? payload[0]?.name)}</div>
      {payload.map((entry, index) => {
        const meta = series.find((s) => s.key === entry.dataKey) ?? series[0];
        return (
          <div key={String(entry.dataKey ?? index)} className="flex items-center gap-2 text-ink-soft">
            <span className="h-2 w-2 rounded-full" style={{ background: entry.color }} />
            <span>{meta?.label ?? entry.name}</span>
            <span className="tabular ml-auto pl-3 font-medium text-ink">
              {formatValue(Number(entry.value), meta?.unit, lang)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function ChartView({ spec, height = 280 }: { spec: ChartSpec; height?: number }) {
  const { lang } = useI18n();
  const xFormat = useXFormatter(spec);
  const unit: Unit = spec.series[0]?.unit ?? "number";
  const data: DataRow[] = spec.data;
  const showLegend = spec.series.length > 1;

  if (spec.type === "pie") {
    const series = spec.series[0];
    const total = data.reduce((sum, row) => sum + (Number(row[series.key]) || 0), 0);
    return (
      <div className="flex flex-col items-center gap-4 sm:flex-row">
        <div style={{ height, width: "100%", maxWidth: height }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie data={data} dataKey={series.key} nameKey={spec.x_key} innerRadius="58%" outerRadius="92%"
                   paddingAngle={1.5} stroke="none" isAnimationActive={false}>
                {data.map((_, index) => (
                  <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip content={(props) => <ChartTooltip {...(props as TooltipContentProps<number, string>)} series={spec.series} xFormat={xFormat} />} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <ul className="w-full space-y-1.5 text-sm">
          {data.map((row, index) => {
            const value = Number(row[series.key]) || 0;
            return (
              <li key={index} className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: CHART_COLORS[index % CHART_COLORS.length] }} />
                <span className="truncate text-ink-soft">{xFormat(row[spec.x_key])}</span>
                <span className="tabular ml-auto text-ink">{formatValue(value, series.unit, lang)}</span>
                <span className="tabular w-12 text-right text-xs text-muted">
                  {total ? `${Math.round((value / total) * 100)}%` : ""}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  const common = {
    data,
    margin: { top: 8, right: 8, bottom: 0, left: 0 },
  };
  const axes = (
    <>
      <CartesianGrid vertical={false} stroke="var(--color-line)" strokeDasharray="0" />
      <XAxis dataKey={spec.x_key} tickFormatter={xFormat} tick={AXIS_TICK} tickLine={false} axisLine={false}
             interval="preserveStartEnd" minTickGap={16} />
      <YAxis tickFormatter={(v: number) => formatAxis(v, unit, lang)} tick={AXIS_TICK} tickLine={false} axisLine={false}
             width={64} />
      <Tooltip cursor={{ fill: "var(--color-sunken)", opacity: 0.6 }}
               content={(props) => <ChartTooltip {...(props as TooltipContentProps<number, string>)} series={spec.series} xFormat={xFormat} />} />
      {showLegend && (
        <Legend verticalAlign="top" align="right" height={28} iconType="circle" iconSize={8}
                formatter={(value: string) => <span className="text-xs text-ink-soft">{spec.series.find((s) => s.key === value)?.label ?? value}</span>} />
      )}
    </>
  );

  return (
    <div style={{ height }}>
      <ResponsiveContainer>
        {spec.type === "bar" ? (
          <BarChart {...common} barCategoryGap="22%">
            {axes}
            {spec.series.map((s, index) => (
              <Bar key={s.key} dataKey={s.key} fill={CHART_COLORS[index % CHART_COLORS.length]} radius={[4, 4, 0, 0]}
                   maxBarSize={48} isAnimationActive={false} />
            ))}
          </BarChart>
        ) : spec.type === "area" ? (
          <AreaChart {...common}>
            <defs>
              {spec.series.map((s, index) => (
                <linearGradient key={s.key} id={`fill-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={CHART_COLORS[index % CHART_COLORS.length]} stopOpacity={0.22} />
                  <stop offset="100%" stopColor={CHART_COLORS[index % CHART_COLORS.length]} stopOpacity={0.02} />
                </linearGradient>
              ))}
            </defs>
            {axes}
            {spec.series.map((s, index) => (
              <Area key={s.key} type="monotone" dataKey={s.key} stroke={CHART_COLORS[index % CHART_COLORS.length]}
                    strokeWidth={2} fill={`url(#fill-${s.key})`} isAnimationActive={false} />
            ))}
          </AreaChart>
        ) : (
          <LineChart {...common}>
            {axes}
            {spec.series.map((s, index) => (
              <Line key={s.key} type="monotone" dataKey={s.key} stroke={CHART_COLORS[index % CHART_COLORS.length]}
                    strokeWidth={2} dot={data.length <= 14 ? { r: 3, strokeWidth: 0, fill: CHART_COLORS[index % CHART_COLORS.length] } : false}
                    isAnimationActive={false} />
            ))}
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

export function Sparkline({ points, tone }: { points: SparklinePoint[]; tone: "positive" | "negative" | "neutral" }) {
  const id = `spark-${useId().replace(/:/g, "")}`;
  if (points.length < 2) return <div className="h-10" />;
  const color = tone === "positive" ? "var(--color-positive)" : tone === "negative" ? "var(--color-negative)" : "var(--color-chart-1)";
  return (
    <div className="h-10">
      <ResponsiveContainer>
        <AreaChart data={points} margin={{ top: 4, right: 0, bottom: 2, left: 0 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.2} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Area type="monotone" dataKey="value" stroke={color} strokeWidth={1.6} fill={`url(#${id})`} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
