"""OpenAI Responses API provider (official OpenAI endpoint).

Reasoning models with function tools require /v1/responses. The conversation is replayed statelessly:
output items (incl. encrypted reasoning) are passed back unchanged, nothing is stored on OpenAI's side.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import openai

from app.llm.base import LLMError, LLMMessage, LLMResponse, LLMUsage, ToolCall, ToolSpec

logger = logging.getLogger(__name__)

_EFFORTS = {"none": "none", "minimal": "minimal", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh",
            "max": "xhigh"}


class OpenAIResponsesProvider:
    def __init__(self, *, api_key: str, model: str, effort: str = "high", timeout_s: float = 90.0,
                 max_retries: int = 2, max_output_tokens: int = 16000, name: str = "openai") -> None:
        self.name = name
        self.model = model
        self._effort: str | None = _EFFORTS.get(effort, "high")
        self._max_output_tokens = max_output_tokens
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout_s, max_retries=max_retries) if api_key else None

    @property
    def configured(self) -> bool:
        return self._client is not None

    def request_params(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": [item for message in messages for item in self._items(message)],
            "max_output_tokens": max_output_tokens or self._max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            params["tools"] = [self._tool(t) for t in tools]
        if self._effort:
            params["reasoning"] = {"effort": self._effort}
        return params

    async def complete(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int | None = None) -> LLMResponse:
        if self._client is None:
            raise LLMError("llm_not_configured", "LLM API key is not configured")
        for attempt in range(3):
            params = self.request_params(system=system, messages=messages, tools=tools, max_output_tokens=max_output_tokens)
            try:
                response = await self._client.responses.create(**params)
                break
            except openai.BadRequestError as exc:
                text = str(exc).lower()
                if "reasoning" in text and self._effort not in (None, "high") and attempt < 2:
                    logger.warning("model rejected reasoning effort, retrying with high", extra={"model": self.model})
                    self._effort = "high"
                    continue
                if "reasoning" in text and self._effort is not None and attempt < 2:
                    logger.warning("model rejected reasoning parameter, retrying without it", extra={"model": self.model})
                    self._effort = None
                    continue
                logger.error("openai responses bad request", extra={"status": exc.status_code, "error": str(exc)[:500]})
                raise LLMError("llm_bad_request", "AI service rejected the request") from exc
            except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
                logger.error("openai auth failed", extra={"status": exc.status_code})
                raise LLMError("llm_unavailable", "AI service rejected the credentials") from exc
            except (openai.NotFoundError, openai.UnprocessableEntityError) as exc:
                logger.error("openai responses rejected", extra={"status": exc.status_code, "error": str(exc)[:500]})
                raise LLMError("llm_bad_request", "AI service rejected the request (model or parameters)") from exc
            except (openai.RateLimitError, openai.APIStatusError, openai.APIConnectionError, openai.APITimeoutError) as exc:
                logger.error("openai unavailable", extra={"error": type(exc).__name__})
                raise LLMError("llm_unavailable", "AI service is unavailable") from exc
        else:  # pragma: no cover
            raise LLMError("llm_bad_request", "AI service rejected the request")

        texts: list[str] = []
        calls: list[ToolCall] = []
        raw: list[dict[str, Any]] = []
        for item in response.output or []:
            raw.append(item.model_dump(mode="json", exclude_none=True, by_alias=True))
            if item.type == "message":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", "") == "output_text":
                        texts.append(part.text)
                    elif getattr(part, "type", "") == "refusal":
                        return self._refusal(response)
            elif item.type == "function_call":
                try:
                    arguments = json.loads(item.arguments or "{}")
                    if not isinstance(arguments, dict):
                        arguments = {"__invalid_arguments__": item.arguments}
                except json.JSONDecodeError:
                    arguments = {"__invalid_arguments__": item.arguments}
                calls.append(ToolCall(id=item.call_id, name=item.name, arguments=arguments))

        stop = "tool_use" if calls else "end_turn"
        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", None)
            if reason == "content_filter":
                return self._refusal(response)
            if not calls:
                stop = "max_tokens"
        return LLMResponse(text="\n".join(texts).strip(), tool_calls=calls, stop_reason=stop,  # type: ignore[arg-type]
                           usage=_usage(response), model=response.model or self.model, provider_raw=raw,
                           provider_name=self.name)

    def _refusal(self, response: Any) -> LLMResponse:
        return LLMResponse(text="", tool_calls=[], stop_reason="refusal", usage=_usage(response),
                           model=getattr(response, "model", self.model), provider_name=self.name)

    def _items(self, message: LLMMessage) -> list[dict[str, Any]]:
        if message.role == "user":
            return [{"role": "user", "content": message.text}]
        if message.role == "tool_results":
            items: list[dict[str, Any]] = [
                {"type": "function_call_output", "call_id": r.tool_call_id,
                 "output": ("ERROR: " + r.content) if r.is_error else r.content}
                for r in message.tool_results
            ]
            if message.text:
                items.append({"role": "user", "content": message.text})
            return items
        if message.provider_name == self.name and message.provider_raw is not None:
            return list(message.provider_raw)
        items = []
        if message.text:
            items.append({"role": "assistant", "content": message.text})
        for call in message.tool_calls:
            items.append({"type": "function_call", "call_id": call.id, "name": call.name,
                          "arguments": json.dumps(call.arguments, ensure_ascii=False)})
        return items

    @staticmethod
    def _tool(tool: ToolSpec) -> dict[str, Any]:
        return {"type": "function", "name": tool.name, "description": tool.description,
                "parameters": tool.input_schema, "strict": tool.strict}


def _usage(response: Any) -> LLMUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return LLMUsage()
    return LLMUsage(input_tokens=getattr(usage, "input_tokens", 0) or 0, output_tokens=getattr(usage, "output_tokens", 0) or 0)
