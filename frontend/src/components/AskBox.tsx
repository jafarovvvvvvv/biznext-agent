import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useI18n } from "../lib/i18n";
import { cx } from "../lib/ui-utils";

export function AskBox({ onAsk, busy, onStop, autoFocus, size = "md", initialValue = "" }: {
  onAsk: (question: string) => void;
  busy?: boolean;
  onStop?: () => void;
  autoFocus?: boolean;
  size?: "md" | "lg";
  initialValue?: string;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState(initialValue);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const question = value.trim();
    if (!question || busy) return;
    onAsk(question);
    setValue("");
  };

  return (
    <form
      onSubmit={submit}
      className={cx(
        "flex items-end gap-2 rounded-2xl border border-line-strong/70 bg-surface shadow-[var(--shadow-float)] transition-colors focus-within:border-ink/40",
        size === "lg" ? "p-2.5 pl-5" : "p-2 pl-4",
      )}
    >
      <textarea
        ref={ref}
        rows={1}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={t("agent.placeholder")}
        aria-label={t("agent.placeholder")}
        maxLength={2000}
        className={cx(
          "max-h-44 flex-1 resize-none bg-transparent py-2 text-ink placeholder:text-faint focus:outline-none",
          size === "lg" ? "text-[17px]" : "text-[15px]",
        )}
      />
      {busy && onStop ? (
        <button type="button" onClick={onStop} className="inline-flex h-10 items-center gap-2 rounded-xl bg-sunken px-4 text-sm font-medium text-ink hover:bg-line">
          <Square className="h-3.5 w-3.5" aria-hidden /> {t("agent.stop")}
        </button>
      ) : (
        <button
          type="submit"
          disabled={!value.trim() || busy}
          className="inline-flex h-10 items-center gap-2 rounded-xl bg-ink px-4 text-sm font-medium text-white transition-colors hover:bg-ink-soft disabled:bg-line-strong"
        >
          {t("agent.ask")} <ArrowUp className="h-4 w-4" aria-hidden />
        </button>
      )}
    </form>
  );
}
