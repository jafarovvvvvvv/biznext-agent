// Типы HTTP API BizNext Agent — строго по docs/ARCHITECTURE.md §8.
// Любое изменение формы — только после правки контракта.

export type Lang = "ru" | "en";

export type Unit = "currency" | "number" | "percent" | "hours" | "liters" | "kg" | "text" | "date";
export type Sentiment = "positive" | "negative" | "warning" | "neutral";
export type Direction = "up" | "down" | "flat";
export type ChartType = "line" | "bar" | "area" | "pie";
export type ColumnType = "text" | "number" | "date" | "currency" | "percent";

export type ApiErrorCode =
  | "invalid_request"
  | "not_found"
  | "llm_not_configured"
  | "llm_unavailable"
  | "database_unavailable"
  | "unauthorized"
  | "internal_error";

/** Тело ошибки: HTTP 4xx/5xx `{"error": {"code", "message"}}` */
export interface ApiErrorBody {
  error: { code: ApiErrorCode | string; message: string };
}

// ---------- 8.1 Служебные ----------

export interface HealthResponse {
  status: "ok" | "degraded";
  database: "ok" | "error";
  llm: "configured" | "not_configured";
}

export interface DateRange {
  start: string;
  end: string;
}

export interface MetaResponse {
  app: string;
  version: string;
  environment: string;
  company: { name: string; industry: string; currency: string; currency_symbol: string };
  timezone: string;
  today: string;
  data_range: DateRange | null;
  llm: { provider: string; model: string; configured: boolean };
  features: { admin_token_required: boolean };
}

// ---------- 8.2 Агент ----------

export interface AskRequest {
  question: string;
  conversation_id: string | null;
  lang: Lang;
}

export type AgentStatus =
  | "answered"
  | "insufficient_data"
  | "clarification_needed"
  | "limit_reached"
  | "error";

export interface Highlight {
  sentiment: Sentiment;
  text: string;
}

export interface AnswerMetric {
  label: string;
  value: number | null;
  unit: Unit;
  formatted: string;
  change_pct: number | null;
  direction: Direction | null;
}

export interface TableColumn {
  key: string;
  label: string;
  type: ColumnType;
}

export type CellValue = string | number | boolean | null;
export type DataRow = Record<string, CellValue>;

export interface AnswerTable {
  title: string;
  columns: TableColumn[];
  rows: DataRow[];
  total_rows: number;
  truncated: boolean;
}

export interface ChartSeries {
  key: string;
  label: string;
  unit: Unit;
}

export interface ChartSpec {
  type: ChartType;
  title: string;
  x_key: string;
  x_type: "date" | "category";
  series: ChartSeries[];
  data: DataRow[];
}

export interface SqlEntry {
  result_id: string;
  purpose: string;
  sql: string;
  row_count: number;
  execution_ms: number;
}

export interface AgentResponse {
  message_id: string;
  conversation_id: string;
  trace_id: string;
  question: string;
  status: AgentStatus;
  summary: string;
  answer: string;
  highlights: Highlight[];
  metrics: AnswerMetric[];
  table: AnswerTable | null;
  chart: ChartSpec | null;
  sources: { tables: string[]; metrics: string[] };
  sql: SqlEntry[];
  limitations: string | null;
  follow_up_questions: string[];
  execution_ms: number;
  created_at: string;
}

export type TraceStepKind =
  | "planning"
  | "schema_inspection"
  | "metric_calculation"
  | "sql_generated"
  | "sql_validation"
  | "sql_execution"
  | "analysis"
  | "final_answer"
  | "retry"
  | "limit_reached"
  | "error";

export interface TraceStepDetail {
  tool?: string | null;
  text?: string | null;
  sql?: string | null;
  purpose?: string | null;
  validation?: { ok: boolean; reason_code: string | null; message: string | null } | null;
  result_id?: string | null;
  row_count?: number | null;
  execution_ms?: number | null;
  metric?: string | null;
  error?: string | null;
}

export interface TraceStep {
  seq: number;
  step: TraceStepKind | string;
  status: "started" | "completed" | "failed";
  title: string;
  started_at: string;
  duration_ms: number | null;
  detail: TraceStepDetail | null;
}

/** SSE `event: start` */
export interface StreamStartEvent {
  trace_id: string;
  conversation_id: string;
  message_id: string;
}

/** SSE `event: error` */
export interface StreamErrorEvent {
  code: string;
  message: string;
  trace_id: string | null;
}

// ---------- 8.3 История и отчёты ----------

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_summary: string | null;
}

export interface ConversationsResponse {
  conversations: ConversationSummary[];
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response: AgentResponse | null;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  messages: ConversationMessage[];
}

export interface ReportItem {
  message_id: string;
  conversation_id: string;
  question: string;
  summary: string;
  status: AgentStatus;
  has_chart: boolean;
  has_table: boolean;
  created_at: string;
}

export interface ReportsResponse {
  reports: ReportItem[];
}

export interface SuggestedQuestion {
  text: string;
  category: string;
}

export interface SuggestedQuestionsResponse {
  questions: SuggestedQuestion[];
}

// ---------- 8.4 Дашборд и инсайты ----------

export interface DashboardPeriod {
  key: string;
  label: string;
  start: string;
  end: string;
  compare_label: string;
  compare_start: string;
  compare_end: string;
  is_partial: boolean;
}

export interface PeriodOption {
  key: string;
  label: string;
}

export interface SparklinePoint {
  period: string;
  value: number | null;
}

export interface Kpi {
  metric: string;
  label: string;
  unit: Unit;
  better: "higher" | "lower";
  value: number | null;
  formatted: string;
  compare_value: number | null;
  change_pct: number | null;
  direction: Direction | null;
  sentiment: Sentiment;
  sparkline: SparklinePoint[];
}

export interface DashboardChart extends ChartSpec {
  key: string;
}

export interface DashboardResponse {
  period: DashboardPeriod;
  available_periods: PeriodOption[];
  kpis: Kpi[];
  charts: DashboardChart[];
  data_range: DateRange | null;
  generated_at: string;
}

export type InsightSeverity = "positive" | "negative" | "warning" | "info";

/** metric_change / dimension_movers дают метрику; sql_check — произвольные строки результата запроса. */
export type InsightFact = Record<string, string | number | boolean | null>;

export interface Insight {
  id: string;
  severity: InsightSeverity;
  title: string;
  text: string;
  facts: InsightFact[];
  suggested_question: string | null;
}

export interface InsightsResponse {
  generated_at: string;
  period: PeriodOption;
  source: "llm" | "deterministic";
  insights: Insight[];
}

// ---------- 8.5 Источники данных ----------

export type DataSourceStatus = "connected" | "error" | "coming_soon";

export interface DataSourceDetails {
  database: string | null;
  schema: string;
  tables?: number;
  total_rows?: number;
  read_only_user: string | null;
  data_start?: string;
  data_end?: string;
  checked_at: string;
}

export interface DataSource {
  id: string;
  type: string;
  name: string;
  status: DataSourceStatus | string;
  description?: string;
  details?: DataSourceDetails;
}

export interface DataSourcesResponse {
  sources: DataSource[];
}

export interface SourceColumn {
  name: string;
  type: string;
  description: string | null;
}

export interface SourceTable {
  name: string;
  description: string | null;
  row_count: number | null;
  columns: SourceColumn[];
}

export interface SourceTablesResponse {
  tables: SourceTable[];
}

// ---------- 8.6 Traces ----------

export interface TraceSummary {
  trace_id: string;
  question: string;
  status: AgentStatus | string;
  created_at: string;
  execution_ms: number | null;
  llm_calls: number;
  tool_calls: number;
  sql_count: number;
  retry_count: number;
}

export interface TracesResponse {
  traces: TraceSummary[];
}

export interface TraceDetail extends TraceSummary {
  conversation_id: string | null;
  message_id: string | null;
  provider: string | null;
  model: string | null;
  usage: { input_tokens: number | null; output_tokens: number | null } | null;
  steps: TraceStep[];
  final_response: AgentResponse | null;
  grounding: { unverified?: string[]; reference_issues?: string[] } | null;
  error: string | null;
}
