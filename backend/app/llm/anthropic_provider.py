from __future__ import annotations

import logging
from typing import Any

import anthropic

from app.llm.base import LLMError, LLMMessage, LLMResponse, LLMUsage, ToolCall, ToolSpec

logger = logging.getLogger(__name__)

# Models that accept adaptive thinking + output_config.effort (no temperature/budget_tokens).
_ADAPTIVE_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5", "claude-sonnet-5",
                      "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5-1")
_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, effort: str = "high", fallbacks: str = "auto",
                 timeout_s: float = 90.0, max_retries: int = 2, max_output_tokens: int = 16000) -> None:
        self.model = model
        self._api_key = api_key
        self._effort = effort if effort in _EFFORTS else "high"
        self._fallbacks = fallbacks
        self._max_output_tokens = max_output_tokens
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=max_retries) if api_key else None

    @property
    def configured(self) -> bool:
        return self._client is not None

    def request_params(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens or self._max_output_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [self._message(m) for m in messages],
        }
        if tools:
            params["tools"] = [self._tool(t) for t in tools]
        if self.model.startswith(_ADAPTIVE_PREFIXES):
            params["thinking"] = {"type": "adaptive"}
            params["output_config"] = {"effort": self._effort}
        use_fallbacks = self._fallbacks == "on" or (self._fallbacks == "auto" and self.model.startswith(_FALLBACK_MODELS))
        if use_fallbacks:
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        return params

    async def complete(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int | None = None) -> LLMResponse:
        if self._client is None:
            raise LLMError("llm_not_configured", "LLM API key is not configured")
        params = self.request_params(system=system, messages=messages, tools=tools, max_output_tokens=max_output_tokens)
        try:
            response = await self._client.beta.messages.create(**params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            logger.error("anthropic auth failed", extra={"status": getattr(exc, "status_code", None)})
            raise LLMError("llm_unavailable", "AI service rejected the credentials") from exc
        except (anthropic.BadRequestError, anthropic.NotFoundError, anthropic.UnprocessableEntityError) as exc:
            logger.error("anthropic bad request", extra={"status": getattr(exc, "status_code", None), "error": str(exc)[:500]})
            raise LLMError("llm_bad_request", "AI service rejected the request") from exc
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError,
                anthropic.APITimeoutError) as exc:
            logger.error("anthropic unavailable", extra={"error": type(exc).__name__})
            raise LLMError("llm_unavailable", "AI service is unavailable") from exc

        stop = response.stop_reason
        if stop == "refusal":
            return LLMResponse(text="", tool_calls=[], stop_reason="refusal", usage=_usage(response),
                               model=response.model, provider_raw=None, provider_name=self.name)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                arguments = block.input if isinstance(block.input, dict) else {}
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(arguments)))
        mapped = {"end_turn": "end_turn", "tool_use": "tool_use", "max_tokens": "max_tokens"}.get(stop or "", "other")
        if calls:
            mapped = "tool_use"
        return LLMResponse(text="\n".join(t for t in text_parts if t).strip(), tool_calls=calls, stop_reason=mapped,
                           usage=_usage(response), model=response.model, provider_raw=list(response.content),
                           provider_name=self.name)

    def _message(self, message: LLMMessage) -> dict[str, Any]:
        if message.role == "user":
            return {"role": "user", "content": message.text}
        if message.role == "tool_results":
            content: list[dict[str, Any]] = [
                {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}
                for r in message.tool_results
            ]
            if message.text:
                content.append({"type": "text", "text": message.text})
            return {"role": "user", "content": content}
        if message.provider_name == self.name and message.provider_raw is not None:
            return {"role": "assistant", "content": message.provider_raw}
        blocks: list[dict[str, Any]] = []
        if message.text:
            blocks.append({"type": "text", "text": message.text})
        for call in message.tool_calls:
            blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
        return {"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]}

    @staticmethod
    def _tool(tool: ToolSpec) -> dict[str, Any]:
        spec: dict[str, Any] = {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        if tool.strict:
            spec["strict"] = True
        return spec


def _usage(response: Any) -> LLMUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return LLMUsage()
    cached = (getattr(usage, "cache_read_input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return LLMUsage(input_tokens=(usage.input_tokens or 0) + cached, output_tokens=usage.output_tokens or 0)
