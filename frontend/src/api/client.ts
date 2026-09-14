import type { ApiErrorBody } from "./types";

export const API_BASE = "/api";

/** Нормализованная ошибка API: код из контракта §8 или клиентский (network_error, bad_response…). */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number | null;

  constructor(code: string, message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

/** Превращает не-2xx ответ в ApiError, по возможности читая тело `{"error": {...}}`. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (isApiErrorBody(body)) {
    return new ApiError(body.error.code, body.error.message, response.status);
  }
  const fallbackCode =
    response.status === 401 || response.status === 403
      ? "unauthorized"
      : response.status === 404
        ? "not_found"
        : response.status >= 500
          ? "internal_error"
          : "invalid_request";
  return new ApiError(fallbackCode, `HTTP ${response.status}`, response.status);
}

export interface RequestOptions {
  signal?: AbortSignal;
  adminToken?: string | null;
  query?: Record<string, string | number | null | undefined>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const params = new URLSearchParams();
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ""}`;
}

function buildHeaders(adminToken?: string | null, extra?: Record<string, string>): Headers {
  const headers = new Headers({ Accept: "application/json", ...extra });
  if (adminToken) headers.set("X-Admin-Token", adminToken);
  return headers;
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers: buildHeaders(options.adminToken),
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError("network_error", "Network request failed");
  }
  if (!response.ok) throw await errorFromResponse(response);
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("bad_response", "Response is not valid JSON", response.status);
  }
}

export function apiGet<T>(path: string, options?: RequestOptions): Promise<T> {
  return request<T>("GET", path, options);
}

export function apiDelete(path: string, options?: RequestOptions): Promise<void> {
  return request<void>("DELETE", path, options);
}

export { buildHeaders, buildUrl };
