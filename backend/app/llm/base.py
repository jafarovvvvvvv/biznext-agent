"""Provider-neutral LLM types. Agent code depends only on this module, never on a provider SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    strict: bool = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class LLMMessage:
    role: Literal["user", "assistant", "tool_results"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # Native provider content that must be replayed unchanged (e.g. Anthropic thinking blocks).
    provider_raw: Any = None
    provider_name: str | None = None

    @classmethod
    def user(cls, text: str) -> "LLMMessage":
        return cls(role="user", text=text)

    @classmethod
    def results(cls, results: list[ToolResult], text: str = "") -> "LLMMessage":
        return cls(role="tool_results", tool_results=results, text=text)


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "LLMUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: LLMUsage
    model: str
    provider_raw: Any = None
    provider_name: str | None = None

    def as_message(self) -> LLMMessage:
        return LLMMessage(role="assistant", text=self.text, tool_calls=list(self.tool_calls),
                          provider_raw=self.provider_raw, provider_name=self.provider_name)


class LLMError(RuntimeError):
    """Normalized provider failure. `code` maps to API error codes."""

    def __init__(self, code: Literal["llm_not_configured", "llm_unavailable", "llm_bad_request"], message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LLMProvider(Protocol):
    name: str
    model: str

    @property
    def configured(self) -> bool: ...

    async def complete(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int | None = None) -> LLMResponse: ...
