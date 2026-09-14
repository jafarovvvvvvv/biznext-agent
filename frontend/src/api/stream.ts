import { ApiError, buildHeaders, buildUrl, errorFromResponse } from "./client";
import { readSseStream, type SseMessage } from "./sse";
import type {
  AgentResponse,
  AskRequest,
  StreamErrorEvent,
  StreamStartEvent,
  TraceStep,
} from "./types";

export interface AskStreamHandlers {
  onStart: (event: StreamStartEvent) => void;
  onStep: (step: TraceStep) => void;
  onFinal: (response: AgentResponse) => void;
  onError: (error: StreamErrorEvent) => void;
}

function parseJson(message: SseMessage): unknown {
  try {
    return JSON.parse(message.data);
  } catch {
    throw new ApiError("bad_response", `Malformed SSE payload for event "${message.event}"`);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * POST /api/agent/ask/stream — события start → step* → final | error (контракт §8.2).
 * Промис резолвится, когда поток закрыт после `final` или `error`.
 * Обрыв потока без финального события — ApiError("stream_interrupted").
 */
export async function askStream(
  body: AskRequest,
  handlers: AskStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(buildUrl("/agent/ask/stream"), {
      method: "POST",
      headers: buildHeaders(null, {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      body: JSON.stringify(body),
      signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError("network_error", "Network request failed");
  }

  if (!response.ok) throw await errorFromResponse(response);
  if (!response.body) throw new ApiError("bad_response", "Empty stream body", response.status);

  let finished = false;

  await readSseStream(response.body, (message) => {
    if (finished) return;
    switch (message.event) {
      case "start": {
        const data = parseJson(message);
        if (isObject(data)) handlers.onStart(data as unknown as StreamStartEvent);
        break;
      }
      case "step": {
        const data = parseJson(message);
        if (isObject(data)) handlers.onStep(data as unknown as TraceStep);
        break;
      }
      case "final": {
        const data = parseJson(message);
        if (!isObject(data)) throw new ApiError("bad_response", "Malformed final event");
        finished = true;
        handlers.onFinal(data as unknown as AgentResponse);
        break;
      }
      case "error": {
        const data = parseJson(message);
        finished = true;
        handlers.onError(
          isObject(data)
            ? {
                code: typeof data.code === "string" ? data.code : "internal_error",
                message: typeof data.message === "string" ? data.message : "",
                trace_id: typeof data.trace_id === "string" ? data.trace_id : null,
              }
            : { code: "internal_error", message: "", trace_id: null },
        );
        break;
      }
      default:
        break; // неизвестные события (например keep-alive "ping") игнорируются
    }
  });

  if (!finished) throw new ApiError("stream_interrupted", "Stream closed before final event");
}
