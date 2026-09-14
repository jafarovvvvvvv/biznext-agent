"""OpenAI Chat Completions provider: OpenAI, OpenRouter, vLLM, Ollama, LM Studio and other compatible APIs."""

from __future__ import annotations

import json
import logging
from typing import Any

import openai

from app.llm.base import LLMError, LLMMessage, LLMResponse, LLMUsage, ToolCall, ToolSpec

logger = logging.getLogger(__name__)

_REASONING_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high", "minimal": "minimal"}


class OpenAICompatibleProvider:
    def __init__(self, *, name: str, api_key: str, model: str, base_url: str = "", effort: str = "high",
                 timeout_s: float = 90.0, max_retries: int = 2, max_output_tokens: int = 16000) -> None:
        self.name = name
        self.model = model
        self._base_url = base_url or None
        self._effort = _REASONING_EFFORT.get(effort, "high")
        self._max_output_tokens = max_output_tokens
        # Local servers (Ollama, vLLM) often need no key; the official endpoint always does.
        needs_key = self._base_url is None
        self._configured = bool(api_key) or not needs_key
        self._client = (
            openai.AsyncOpenAI(api_key=api_key or "not-needed", base_url=self._base_url, timeout=timeout_s,
                               max_retries=max_retries)
            if self._configured else None
        )
        self._send_reasoning_effort = True
        self._token_param = "max_completion_tokens"

    @property
    def configured(self) -> bool:
        return self._configured

    def request_params(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + [m for msg in messages for m in self._messages(msg)],
            self._token_param: max_output_tokens or self._max_output_tokens,
        }
        if tools:
            params["tools"] = [self._tool(t) for t in tools]
        if self._send_reasoning_effort:
            params["reasoning_effort"] = self._effort
        return params

    async def complete(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int | None = None) -> LLMResponse:
        if self._client is None:
            raise LLMError("llm_not_configured", "LLM API key is not configured")
        for attempt in range(3):
            params = self.request_params(system=system, messages=messages, tools=tools, max_output_tokens=max_output_tokens)
            try:
                completion = await self._client.chat.completions.create(**params)
                break
            except openai.BadRequestError as exc:
                text = str(exc).lower()
                if self._send_reasoning_effort and "reasoning" in text and attempt < 2:
                    logger.warning("model rejected reasoning_effort, retrying without it", extra={"model": self.model})
                    self._send_reasoning_effort = False
                    continue
                if self._token_param == "max_completion_tokens" and "max_completion_tokens" in text and attempt < 2:
                    logger.warning("model rejected max_completion_tokens, retrying with max_tokens", extra={"model": self.model})
                    self._token_param = "max_tokens"
                    continue
                logger.error("openai bad request", extra={"status": exc.status_code, "error": str(exc)[:500]})
                raise LLMError("llm_bad_request", "AI service rejected the request") from exc
            except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
                logger.error("openai auth failed", extra={"status": exc.status_code})
                raise LLMError("llm_unavailable", "AI service rejected the credentials") from exc
            except (openai.NotFoundError, openai.UnprocessableEntityError) as exc:
                logger.error("openai request rejected", extra={"status": exc.status_code, "error": str(exc)[:500]})
                raise LLMError("llm_bad_request", "AI service rejected the request (model or parameters)") from exc
            except (openai.RateLimitError, openai.APIStatusError, openai.APIConnectionError, openai.APITimeoutError) as exc:
                logger.error("openai unavailable", extra={"error": type(exc).__name__})
                raise LLMError("llm_unavailable", "AI service is unavailable") from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise LLMError("llm_bad_request", "AI service rejected the request")

        if not completion.choices:
            raise LLMError("llm_unavailable", "AI service returned an empty response")
        choice = completion.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        for call in message.tool_calls or []:
            function = getattr(call, "function", None)
            if function is None:
                continue
            try:
                arguments = json.loads(function.arguments or "{}")
                if not isinstance(arguments, dict):
                    arguments = {"__invalid_arguments__": function.arguments}
            except json.JSONDecodeError:
                arguments = {"__invalid_arguments__": function.arguments}
            calls.append(ToolCall(id=call.id, name=function.name, arguments=arguments))
        finish = choice.finish_reason
        stop = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}.get(
            finish or "", "other")
        if calls:
            stop = "tool_use"
        usage = completion.usage
        return LLMResponse(
            text=(message.content or "").strip(),
            tool_calls=calls,
            stop_reason=stop,  # type: ignore[arg-type]
            usage=LLMUsage(input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                           output_tokens=getattr(usage, "completion_tokens", 0) or 0),
            model=completion.model or self.model,
            provider_name=self.name,
        )

    @staticmethod
    def _messages(message: LLMMessage) -> list[dict[str, Any]]:
        if message.role == "user":
            return [{"role": "user", "content": message.text}]
        if message.role == "tool_results":
            items: list[dict[str, Any]] = [
                {"role": "tool", "tool_call_id": r.tool_call_id,
                 "content": ("ERROR: " + r.content) if r.is_error else r.content}
                for r in message.tool_results
            ]
            if message.text:
                items.append({"role": "user", "content": message.text})
            return items
        item: dict[str, Any] = {"role": "assistant", "content": message.text or None}
        if message.tool_calls:
            item["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in message.tool_calls
            ]
        return [item]

    @staticmethod
    def _tool(tool: ToolSpec) -> dict[str, Any]:
        function: dict[str, Any] = {"name": tool.name, "description": tool.description, "parameters": tool.input_schema}
        if tool.strict:
            function["strict"] = True
        return {"type": "function", "function": function}
