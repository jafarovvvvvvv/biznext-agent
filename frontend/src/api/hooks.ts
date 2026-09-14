import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useI18n } from "../lib/i18n";
import { useSettings } from "../lib/settings";
import { apiGet } from "./client";
import type {
  ConversationDetail,
  ConversationsResponse,
  DashboardResponse,
  DataSourcesResponse,
  InsightsResponse,
  MetaResponse,
  ReportsResponse,
  SourceTablesResponse,
  SuggestedQuestionsResponse,
  TraceDetail,
  TracesResponse,
} from "./types";

export const queryKeys = {
  conversations: ["conversations"] as const,
  conversation: (id: string) => ["conversation", id] as const,
  reports: ["reports"] as const,
};

export function useMeta() {
  const { lang } = useI18n();
  return useQuery({
    queryKey: ["meta", lang],
    queryFn: ({ signal }) => apiGet<MetaResponse>("/meta", { signal, query: { lang } }),
    staleTime: 60_000,
  });
}

export function useDashboard(period: string | null) {
  const { lang } = useI18n();
  return useQuery({
    queryKey: ["dashboard", period, lang],
    queryFn: ({ signal }) => apiGet<DashboardResponse>("/dashboard", { signal, query: { period, lang } }),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

export function useInsights(period: string | null) {
  const { lang } = useI18n();
  return useQuery({
    queryKey: ["insights", period, lang],
    queryFn: ({ signal }) => apiGet<InsightsResponse>("/insights", { signal, query: { period, lang } }),
    staleTime: 5 * 60_000,
  });
}

export function useSuggestedQuestions() {
  const { lang } = useI18n();
  return useQuery({
    queryKey: ["suggested", lang],
    queryFn: ({ signal }) => apiGet<SuggestedQuestionsResponse>("/suggested-questions", { signal, query: { lang } }),
    staleTime: 10 * 60_000,
  });
}

export function useConversations() {
  return useQuery({
    queryKey: queryKeys.conversations,
    queryFn: ({ signal }) => apiGet<ConversationsResponse>("/conversations", { signal }),
  });
}

export function useConversation(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.conversation(id ?? ""),
    queryFn: ({ signal }) => apiGet<ConversationDetail>(`/conversations/${id}`, { signal }),
    enabled: Boolean(id),
    staleTime: Infinity,
  });
}

export function useReports() {
  return useQuery({
    queryKey: queryKeys.reports,
    queryFn: ({ signal }) => apiGet<ReportsResponse>("/reports", { signal, query: { limit: 100 } }),
  });
}

export function useDataSources() {
  const { lang } = useI18n();
  return useQuery({
    queryKey: ["data-sources", lang],
    queryFn: ({ signal }) => apiGet<DataSourcesResponse>("/data-sources", { signal, query: { lang } }),
  });
}

export function useSourceTables(sourceId: string | null) {
  const { lang } = useI18n();
  return useQuery({
    queryKey: ["source-tables", sourceId, lang],
    queryFn: ({ signal }) => apiGet<SourceTablesResponse>(`/data-sources/${sourceId}/tables`, { signal, query: { lang } }),
    enabled: Boolean(sourceId),
    staleTime: 5 * 60_000,
  });
}

export function useTraces() {
  const { adminToken } = useSettings();
  return useQuery({
    queryKey: ["traces", adminToken],
    queryFn: ({ signal }) => apiGet<TracesResponse>("/traces", { signal, adminToken, query: { limit: 200 } }),
    retry: false,
  });
}

export function useTrace(traceId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: ["trace", traceId],
    queryFn: ({ signal }) => apiGet<TraceDetail>(`/traces/${traceId}`, { signal }),
    enabled: Boolean(traceId) && enabled,
    staleTime: Infinity,
  });
}
