import { AlertTriangle, ArrowDownRight, ArrowUpRight, Check, Copy, Minus, RefreshCw } from "lucide-react";
import { useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { Direction, Sentiment } from "../api/types";
import { formatChangePct } from "../lib/format";
import { useI18n } from "../lib/i18n";
import { cx, errorMessage } from "../lib/ui-utils";

type ButtonVariant = "primary" | "secondary" | "ghost";

export function Button({
  variant = "secondary",
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  const styles: Record<ButtonVariant, string> = {
    primary: "bg-ink text-white hover:bg-ink-soft disabled:bg-line-strong",
    secondary: "bg-surface text-ink border border-line hover:border-line-strong hover:bg-sunken/60",
    ghost: "text-ink-soft hover:bg-sunken hover:text-ink",
  };
  return (
    <button
      type="button"
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed",
        styles[variant],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx("card", className)}>{children}</div>;
}

export function PageHeader({ eyebrow, title, subtitle, actions }: {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && <div className="eyebrow mb-2">{eyebrow}</div>}
        <h1 className="font-display text-[28px] font-semibold leading-tight tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1.5 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-sunken", className)} />;
}

export function StatusDot({ tone }: { tone: "positive" | "negative" | "muted" | "accent" }) {
  const colors = { positive: "bg-positive", negative: "bg-negative", muted: "bg-faint", accent: "bg-accent" };
  return <span className={cx("inline-block h-2 w-2 shrink-0 rounded-full", colors[tone])} />;
}

const SENTIMENT_STYLES: Record<Sentiment | "info", string> = {
  positive: "bg-positive-soft text-positive",
  negative: "bg-negative-soft text-negative",
  warning: "bg-warning-soft text-warning",
  neutral: "bg-sunken text-ink-soft",
  info: "bg-accent-soft text-accent-ink",
};

export function Pill({ tone = "neutral", children, className }: {
  tone?: Sentiment | "info";
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", SENTIMENT_STYLES[tone], className)}>
      {children}
    </span>
  );
}

export function Delta({ changePct, direction, sentiment, className }: {
  changePct: number | null;
  direction: Direction | null;
  sentiment?: Sentiment;
  className?: string;
}) {
  const { lang } = useI18n();
  if (changePct === null || direction === null) return <span className={cx("text-xs text-faint", className)}>—</span>;
  const tone: Sentiment = sentiment ?? (direction === "up" ? "positive" : direction === "down" ? "negative" : "neutral");
  const Icon = direction === "up" ? ArrowUpRight : direction === "down" ? ArrowDownRight : Minus;
  return (
    <Pill tone={tone} className={cx("tabular", className)}>
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {formatChangePct(changePct, lang)}
    </Pill>
  );
}

export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const { t } = useI18n();
  const message = errorMessage(error, t);
  return (
    <div className={cx("flex flex-col items-center justify-center gap-3 text-center", compact ? "py-6" : "py-16")}>
      <AlertTriangle className="h-6 w-6 text-warning" aria-hidden />
      <div>
        <div className="text-sm font-medium text-ink">{t("common.error")}</div>
        <div className="mt-1 text-xs text-muted">{message}</div>
      </div>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden /> {t("common.retry")}
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ icon, title, text, children }: {
  icon?: ReactNode;
  title: string;
  text?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      {icon && <div className="text-faint">{icon}</div>}
      <div className="text-sm font-medium text-ink">{title}</div>
      {text && <div className="max-w-sm text-sm text-muted">{text}</div>}
      {children}
    </div>
  );
}

export function CopyButton({ text }: { text: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted hover:bg-sunken hover:text-ink"
    >
      {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      {copied ? t("common.copied") : t("common.copy")}
    </button>
  );
}
