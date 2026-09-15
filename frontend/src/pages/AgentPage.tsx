import { useQueryClient } from "@tanstack/react-query";
import { Check, MessageSquarePlus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { ApiError, apiDelete } from "../api/client";
import { queryKeys, useActiveSource, useConversation, useConversations, useMeta, useSuggestedQuestions } from "../api/hooks";
import { askStream } from "../api/stream";
import type { AgentResponse, TraceStep } from "../api/types";
import { AnswerCard } from "../components/answer/AnswerCard";
import { AskBox } from "../components/AskBox";
import { SourceIcon, SourceTag } from "../components/SourcePicker";
import { ErrorState, Skeleton } from "../components/ui";
import { cx } from "../lib/ui-utils";
import { dayGroup, type DayGroup } from "../lib/format";
import { useI18n, type MessageKey } from "../lib/i18n";

interface Turn {
  key: string;
  question: string;
  response?: AgentResponse;
  steps: TraceStep[];
  running: boolean;
  error?: { code: string; message: string };
  startedAt: number;
}

const PROGRESS_ORDER = ["planning", "schema_inspection", "metric_calculation", "sql_generated", "sql_validation", "sql_execution", "retry", "analysis", "final_answer"];

function Progress({ turn }: { turn: Turn }) {
  const { t } = useI18n();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  const seen: string[] = [];
  for (const step of turn.steps) {
    const kind = step.step === "sql_generated" || step.step === "sql_validation" ? step.step : step.step;
    if (PROGRESS_ORDER.includes(kind) && !seen.includes(kind)) seen.push(kind);
  }
  const current = turn.steps[turn.steps.length - 1];
  const stages = seen.length ? seen : ["start"];
  return (
    <div className="card px-6 py-5 md:px-8">
      <div className="flex items-center gap-3">
        <span className="flex gap-1" aria-hidden>
          {[0, 1, 2].map((i) => (
            <span key={i} className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-accent" style={{ animationDelay: `${i * 0.18}s` }} />
          ))}
        </span>
        <span className="text-sm font-medium text-ink">{t("agent.working")}</span>
        <span className="tabular ml-auto text-xs text-faint">{((now - turn.startedAt) / 1000).toFixed(0)} {t("agent.seconds")}</span>
      </div>
      <ol className="mt-4 space-y-2">
        {stages.map((stage, index) => {
          const active = index === stages.length - 1;
          return (
            <li key={stage} className={cx("flex items-center gap-2.5 text-sm", active ? "text-ink" : "text-muted")}>
              {active ? (
                <span className="grid h-4 w-4 place-items-center"><span className="animate-pulse-dot h-2 w-2 rounded-full bg-accent" /></span>
              ) : (
                <Check className="h-4 w-4 text-positive" aria-hidden />
              )}
              {t(`progress.${stage}` as MessageKey)}
              {active && current?.detail?.metric && <span className="font-mono text-xs text-faint">{current.detail.metric}</span>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function TurnView({ turn, onFollowUp }: { turn: Turn; onFollowUp: (q: string) => void }) {
  const { t } = useI18n();
  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-[15px] leading-relaxed text-white">{turn.question}</div>
      </div>
      {turn.response ? (
        <AnswerCard response={turn.response} onFollowUp={onFollowUp} />
      ) : turn.running ? (
        <Progress turn={turn} />
      ) : turn.error ? (
        <div className="card border-negative/30 px-6 py-5">
          <div className="text-sm font-medium text-negative">
            {turn.error.code === "llm_not_configured" ? t("agent.llmNotConfigured") : t("agent.statusError")}
          </div>
          <div className="mt-1 text-sm text-ink-soft">{turn.error.message || t("error.generic")}</div>
        </div>
      ) : null}
    </section>
  );
}

function History({ activeId, onNew }: { activeId?: string; onNew: () => void }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const conversations = useConversations();
  const { sources } = useActiveSource();
  const list = conversations.data?.conversations;
  const groups = useMemo(() => {
    const result: Record<DayGroup, NonNullable<typeof list>> = { today: [], yesterday: [], earlier: [] };
    for (const c of list ?? []) result[dayGroup(c.updated_at)].push(c);
    return result;
  }, [list]);
  const labels: Record<DayGroup, MessageKey> = { today: "agent.today", yesterday: "agent.yesterday", earlier: "agent.earlier" };

  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-line lg:flex">
      <div className="p-3">
        <button type="button" onClick={onNew}
                className="flex w-full items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm font-medium text-ink hover:border-line-strong">
          <MessageSquarePlus className="h-4 w-4" aria-hidden /> {t("agent.newChat")}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-6">
        {conversations.isLoading && <div className="space-y-2 p-2"><Skeleton className="h-8" /><Skeleton className="h-8" /></div>}
        {conversations.data && conversations.data.conversations.length === 0 && (
          <div className="px-3 py-6 text-xs text-muted">{t("agent.noHistory")}</div>
        )}
        {(Object.keys(groups) as DayGroup[]).map((group) =>
          groups[group].length ? (
            <div key={group} className="mt-3">
              <div className="eyebrow px-3 pb-1">{t(labels[group])}</div>
              {groups[group].map((c) => (
                <div key={c.id} className={cx("group flex items-center rounded-lg", c.id === activeId ? "bg-surface ring-1 ring-line" : "hover:bg-sunken")}>
                  <button type="button" onClick={() => navigate(`/agent/${c.id}`)} className="flex min-w-0 flex-1 items-center gap-1.5 px-3 py-2 text-left text-[13px] text-ink-soft" title={c.title}>
                    <span className="truncate">{c.title}</span>
                    {sources.length > 1 && <SourceTag source={sources.find((s) => s.id === (c.source_id ?? sources.find((x) => x.is_default)?.id))} />}
                  </button>
                  <button
                    type="button"
                    aria-label={t("agent.deleteChat")}
                    onClick={async () => {
                      await apiDelete(`/conversations/${c.id}`);
                      await queryClient.invalidateQueries({ queryKey: queryKeys.conversations });
                      await queryClient.invalidateQueries({ queryKey: queryKeys.reports });
                      if (c.id === activeId) onNew();
                    }}
                    className="mr-1 hidden rounded p-1 text-faint hover:text-negative group-hover:block"
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </div>
              ))}
            </div>
          ) : null,
        )}
      </div>
    </aside>
  );
}

export function AgentPage() {
  const { t, lang } = useI18n();
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const meta = useMeta();
  const active = useActiveSource();
  const suggested = useSuggestedQuestions();
  // conversation created by a question asked on this screen: its turns are already in local state
  const [createdHere, setCreatedHere] = useState<string | null>(null);
  const conversation = useConversation(conversationId && conversationId !== createdHere ? conversationId : undefined);
  // turns streamed on this screen, bound to the conversation they belong to (null = new conversation)
  const [local, setLocal] = useState<{ owner: string | null; turns: Turn[] }>({ owner: null, turns: [] });
  const activeOwner = conversationId ?? null;
  const turns = local.owner === activeOwner ? local.turns : [];
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const autoAsked = useRef(false);

  const stored: Turn[] = useMemo(() => {
    const messages = conversation.data?.messages ?? [];
    const result: Turn[] = [];
    for (const message of messages) {
      if (message.role === "user") {
        result.push({ key: message.id, question: message.content, steps: [], running: false, startedAt: 0 });
      } else if (result.length) {
        const last = result[result.length - 1];
        if (message.response) last.response = message.response;
        else last.error = { code: "internal_error", message: message.content };
      }
    }
    return result;
  }, [conversation.data]);

  const allTurns = conversationId && conversationId !== createdHere ? [...stored, ...turns] : turns;
  const running = turns.some((turn) => turn.running);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [allTurns.length, running]);

  const update = (key: string, patch: (turn: Turn) => Turn) =>
    setLocal((current) => ({ ...current, turns: current.turns.map((turn) => (turn.key === key ? patch(turn) : turn)) }));

  const ask = useCallback(
    async (question: string) => {
      if (running) return;
      const key = `local-${Date.now()}`;
      const turn: Turn = { key, question, steps: [], running: true, startedAt: Date.now() };
      setLocal((current) => ({ owner: activeOwner, turns: [...(current.owner === activeOwner ? current.turns : []), turn] }));
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await askStream(
          { question, conversation_id: conversationId ?? null, lang, source_id: active.sourceId || null },
          {
            onStart: (event) => {
              if (!conversationId) {
                setCreatedHere(event.conversation_id);
                setLocal((current) => ({ ...current, owner: event.conversation_id }));
                navigate(`/agent/${event.conversation_id}`, { replace: true });
              }
            },
            onStep: (step) =>
              update(key, (turn) => {
                const steps = [...turn.steps];
                const index = steps.findIndex((s) => s.seq === step.seq);
                if (index >= 0) steps[index] = step;
                else steps.push(step);
                return { ...turn, steps };
              }),
            onFinal: (response) => update(key, (turn) => ({ ...turn, response, running: false })),
            onError: (error) => update(key, (turn) => ({ ...turn, running: false, error })),
          },
          controller.signal,
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          update(key, (turn) => ({ ...turn, running: false, error: { code: "aborted", message: "" } }));
        } else {
          const message = error instanceof ApiError ? error.message : t("error.generic");
          const code = error instanceof ApiError ? error.code : "internal_error";
          update(key, (turn) => ({ ...turn, running: false, error: { code, message } }));
        }
      } finally {
        abortRef.current = null;
        void queryClient.invalidateQueries({ queryKey: queryKeys.conversations });
        void queryClient.invalidateQueries({ queryKey: queryKeys.reports });
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [conversationId, lang, running, active.sourceId],
  );

  // question passed from the dashboard ("Ask your data")
  useEffect(() => {
    const question = (location.state as { question?: string } | null)?.question;
    if (question && !autoAsked.current) {
      autoAsked.current = true;
      navigate(location.pathname, { replace: true, state: null });
      void ask(question);
    }
  }, [location, ask, navigate]);

  const startNew = () => {
    abortRef.current?.abort();
    setCreatedHere(null);
    setLocal({ owner: null, turns: [] });
    navigate("/agent");
  };

  const empty = allTurns.length === 0 && !conversation.isLoading;
  const viewSourceId = conversation.data?.source_id ?? allTurns.find((turn) => turn.response?.source_id)?.response?.source_id ?? null;
  const viewSource = active.sources.find((s) => s.id === (viewSourceId ?? active.sources.find((x) => x.is_default)?.id));

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      <History activeId={conversationId} onNew={startNew} />
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          {empty ? (
            <div className="mx-auto flex max-w-3xl flex-col px-6 pt-[12vh] pb-10">
              <div className="eyebrow">{t("agent.heroEyebrow")}</div>
              <h1 className="mt-3 font-display text-[40px] font-semibold leading-[1.05] tracking-tight text-ink md:text-[48px]">
                {t("agent.heroTitle")}
              </h1>
              <p className="mt-4 max-w-2xl text-[16px] leading-relaxed text-muted">{t("agent.heroText")}</p>
              {active.source && active.sources.length > 1 && (
                <div className="mt-4 inline-flex w-fit items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs text-ink-soft">
                  <SourceIcon type={active.source.type} className={active.source.type === "1c" ? "text-warning" : "text-accent"} />
                  {t("agent.sourceLabel")}: <span className="font-medium text-ink">{active.source.name}</span>
                </div>
              )}
              {meta.data && !meta.data.llm.configured && (
                <div className="mt-4 rounded-lg bg-warning-soft px-3 py-2 text-sm text-warning">{t("agent.llmNotConfigured")}</div>
              )}
              <div className="mt-8">
                <AskBox onAsk={(q) => void ask(q)} autoFocus size="lg" />
              </div>
              <div className="mt-10">
                <div className="eyebrow mb-3">{t("agent.suggested")}</div>
                {suggested.isLoading && <div className="grid gap-2 sm:grid-cols-2"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}
                {suggested.error && <ErrorState error={suggested.error} compact />}
                <div className="grid gap-2 sm:grid-cols-2">
                  {suggested.data?.questions.map((q) => (
                    <button
                      key={q.text}
                      type="button"
                      onClick={() => void ask(q.text)}
                      className="rounded-xl border border-line bg-surface px-4 py-3 text-left text-sm text-ink-soft transition-all hover:-translate-y-px hover:border-line-strong hover:text-ink hover:shadow-[var(--shadow-card)]"
                    >
                      {q.text}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-4xl space-y-10 px-4 pt-8 pb-40 md:px-8">
              {viewSource && active.sources.length > 1 && (
                <div className="!mb-4 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <SourceIcon type={viewSource.type} className={viewSource.type === "1c" ? "text-warning" : "text-accent"} />
                  {t("agent.sourceLabel")}: <span className="font-medium text-ink-soft">{viewSource.name}</span>
                  {viewSource.id !== active.sourceId && <span className="text-faint">· {t("agent.sourceLocked")}</span>}
                </div>
              )}
              {conversation.isLoading && <Skeleton className="h-40" />}
              {conversation.error && <ErrorState error={conversation.error} onRetry={() => void conversation.refetch()} />}
              {allTurns.map((turn) => (
                <TurnView key={turn.key} turn={turn} onFollowUp={(q) => void ask(q)} />
              ))}
              <div ref={bottomRef} />
            </div>
          )}
        </div>
        {!empty && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-paper via-paper/95 to-transparent pt-10 pb-5">
            <div className="pointer-events-auto mx-auto max-w-4xl px-4 md:px-8">
              <AskBox onAsk={(q) => void ask(q)} busy={running} onStop={() => abortRef.current?.abort()} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
