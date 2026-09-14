import type { Lang, Unit } from "../api/types";

const MINUS = "−";
const NBSP = " ";

function locale(lang: Lang): string {
  return lang === "en" ? "en-US" : "ru-RU";
}

function number(value: number, lang: Lang, digits: number): string {
  const text = new Intl.NumberFormat(locale(lang), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Math.abs(value));
  return (value < 0 ? (lang === "ru" ? MINUS : "-") : "") + text;
}

function compact(value: number, lang: Lang): { scaled: number; suffix: string } {
  const size = Math.abs(value);
  const steps: [number, string, string][] = [
    [1e9, "млрд", "B"],
    [1e6, "млн", "M"],
    [1e3, "тыс.", "K"],
  ];
  for (const [threshold, ru, en] of steps) {
    if (size >= threshold) return { scaled: value / threshold, suffix: lang === "en" ? en : ru };
  }
  return { scaled: value, suffix: "" };
}

export function formatValue(
  value: number | null | undefined,
  unit: Unit | string | null | undefined,
  lang: Lang,
  currency = "₸",
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  switch (unit) {
    case "percent":
      return lang === "en" ? `${number(value, lang, 1)}%` : `${number(value, lang, 1)}${NBSP}%`;
    case "currency": {
      const { scaled, suffix } = compact(value, lang);
      const digits = suffix ? (Math.abs(scaled) >= 10 ? 1 : 2) : 0;
      if (lang === "en") {
        const body = number(scaled, lang, digits);
        return body.startsWith("-") ? `-${currency}${body.slice(1)}${suffix}` : `${currency}${body}${suffix}`;
      }
      return suffix
        ? `${number(scaled, lang, digits)}${NBSP}${suffix}${NBSP}${currency}`
        : `${number(scaled, lang, 0)}${NBSP}${currency}`;
    }
    case "hours":
      return `${number(value, lang, 1)}${NBSP}${lang === "en" ? "h" : "ч"}`;
    case "liters":
    case "kg": {
      const { scaled, suffix } = compact(value, lang);
      const label = unit === "kg" ? (lang === "en" ? "kg" : "кг") : lang === "en" ? "L" : "л";
      return `${number(scaled, lang, suffix ? 1 : 0)}${suffix ? ` ${suffix}` : ""}${NBSP}${label}`;
    }
    default: {
      if (Math.abs(value) >= 1e6) {
        const { scaled, suffix } = compact(value, lang);
        return `${number(scaled, lang, 1)}${NBSP}${suffix}`;
      }
      return number(value, lang, Number.isInteger(value) ? 0 : Math.abs(value) < 10 ? 2 : 1);
    }
  }
}

/** Short axis labels: 412 млн, 1,2 млрд, 35 %. */
export function formatAxis(value: number, unit: Unit | string | null | undefined, lang: Lang): string {
  if (unit === "percent") return lang === "en" ? `${Math.round(value)}%` : `${Math.round(value)}${NBSP}%`;
  const { scaled, suffix } = compact(value, lang);
  const digits = suffix && Math.abs(scaled) < 10 && !Number.isInteger(scaled) ? 1 : 0;
  return `${number(scaled, lang, digits)}${suffix ? NBSP + suffix : ""}`;
}

export function formatChangePct(value: number | null | undefined, lang: Lang): string {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return lang === "en" ? `${sign}${number(value, lang, 1)}%` : `${sign}${number(value, lang, 1)}${NBSP}%`;
}

export function parseDate(value: string): Date {
  // plain dates are calendar dates, not UTC instants
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`) : new Date(value);
}

export function formatDate(value: string | null | undefined, lang: Lang): string {
  if (!value) return "—";
  return parseDate(value).toLocaleDateString(locale(lang), { day: "numeric", month: "short", year: "numeric" });
}

export function formatMonth(value: string, lang: Lang, short = false): string {
  const date = parseDate(value);
  const text = date.toLocaleDateString(locale(lang), { month: short ? "short" : "long", year: short ? "2-digit" : "numeric" });
  return text.replace(" г.", "").replace(".", "");
}

export function formatDateTime(value: string | null | undefined, lang: Lang): string {
  if (!value) return "—";
  return parseDate(value).toLocaleString(locale(lang), {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTime(value: string, lang: Lang): string {
  return parseDate(value).toLocaleTimeString(locale(lang), { hour: "2-digit", minute: "2-digit" });
}

export function formatDuration(ms: number | null | undefined, lang: Lang): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}${NBSP}${lang === "en" ? "ms" : "мс"}`;
  return `${number(ms / 1000, lang, 1)}${NBSP}${lang === "en" ? "s" : "с"}`;
}

export type DayGroup = "today" | "yesterday" | "earlier";

export function dayGroup(value: string, now = new Date()): DayGroup {
  const date = parseDate(value);
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (date >= start) return "today";
  const yesterday = new Date(start);
  yesterday.setDate(start.getDate() - 1);
  return date >= yesterday ? "yesterday" : "earlier";
}

/** Formats a table/chart cell by column type. */
export function formatCell(value: unknown, type: string, lang: Lang, currency = "₸"): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") {
    if (type === "currency") return formatValue(value, "currency", lang, currency);
    if (type === "percent") return formatValue(value, "percent", lang);
    return number(value, lang, Number.isInteger(value) ? 0 : 2);
  }
  if (type === "date" && typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value)) {
    return formatDate(value.slice(0, 10), lang);
  }
  return String(value);
}
