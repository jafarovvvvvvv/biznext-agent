from app.config import Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMMessage, ToolCall, ToolResult, ToolSpec
from app.llm.factory import build_provider
from app.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.llm.openai_responses_provider import OpenAIResponsesProvider

TOOLS = [ToolSpec("run_readonly_sql", "run", {"type": "object", "properties": {}}),
         ToolSpec("submit_answer", "final", {"type": "object", "properties": {}}, strict=True)]


def conversation(provider_name: str | None, raw: object = None) -> list[LLMMessage]:
    return [
        LLMMessage.user("question"),
        LLMMessage(role="assistant", text="plan", tool_calls=[ToolCall("t1", "run_readonly_sql", {"sql": "SELECT 1"})],
                   provider_raw=raw, provider_name=provider_name),
        LLMMessage.results([ToolResult("t1", '{"rows":[]}', is_error=True)], text="reminder"),
    ]


def test_anthropic_params_for_opus_5() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-opus-5", effort="high")
    params = provider.request_params(system="sys", messages=conversation(None), tools=TOOLS, max_output_tokens=None)
    assert params["thinking"] == {"type": "adaptive"} and params["output_config"] == {"effort": "high"}
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in params and "budget_tokens" not in str(params)
    assert params["tools"][1]["strict"] is True and "strict" not in params["tools"][0]
    assistant = params["messages"][1]["content"]
    assert assistant[1] == {"type": "tool_use", "id": "t1", "name": "run_readonly_sql", "input": {"sql": "SELECT 1"}}
    results = params["messages"][2]["content"]
    assert results[0]["type"] == "tool_result" and results[0]["is_error"] is True and results[1]["text"] == "reminder"


def test_anthropic_replays_native_content_unchanged() -> None:
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]
    provider = AnthropicProvider(api_key="k", model="claude-opus-5")
    params = provider.request_params(system="s", messages=conversation("anthropic", raw), tools=[], max_output_tokens=10)
    assert params["messages"][1]["content"] is raw


def test_anthropic_older_model_without_adaptive_or_fallbacks() -> None:
    provider = AnthropicProvider(api_key="k", model="claude-haiku-4-5")
    params = provider.request_params(system="s", messages=[LLMMessage.user("q")], tools=[], max_output_tokens=10)
    assert "thinking" not in params and "fallbacks" not in params and "output_config" not in params


def test_openai_message_mapping() -> None:
    provider = OpenAICompatibleProvider(name="openai", api_key="k", model="gpt-x", effort="xhigh")
    params = provider.request_params(system="sys", messages=conversation("anthropic", ["ignored"]), tools=TOOLS,
                                     max_output_tokens=None)
    messages = params["messages"]
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[2]["tool_calls"][0]["function"] == {"name": "run_readonly_sql", "arguments": '{"sql": "SELECT 1"}'}
    assert messages[3] == {"role": "tool", "tool_call_id": "t1", "content": 'ERROR: {"rows":[]}'}
    assert messages[4] == {"role": "user", "content": "reminder"}
    assert params["reasoning_effort"] == "high" and "max_completion_tokens" in params and "temperature" not in params
    assert params["tools"][1]["function"]["strict"] is True


def test_factory_and_configuration() -> None:
    base = dict(database_url="", agent_database_url="")
    assert build_provider(Settings(llm_provider="anthropic", llm_api_key="", **base)).configured is False
    openai = build_provider(Settings(llm_provider="openai", llm_model="gpt-x", llm_api_key="k", **base))
    assert isinstance(openai, OpenAIResponsesProvider) and openai.configured
    local = build_provider(Settings(llm_provider="openai_compatible", llm_base_url="http://ollama:11434/v1", llm_api_key="", **base))
    assert local.configured  # local servers need no key


def test_openai_responses_mapping() -> None:
    provider = OpenAIResponsesProvider(api_key="k", model="gpt-5.6-terra", effort="max")
    raw = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "enc", "summary": []},
           {"type": "function_call", "call_id": "t1", "name": "run_readonly_sql", "arguments": "{}"}]
    params = provider.request_params(system="sys", messages=conversation("openai", raw), tools=TOOLS, max_output_tokens=None)
    assert params["instructions"] == "sys" and params["store"] is False
    assert params["include"] == ["reasoning.encrypted_content"] and params["reasoning"] == {"effort": "xhigh"}
    items = params["input"]
    assert items[0] == {"role": "user", "content": "question"}
    assert items[1:3] == raw  # native output replayed unchanged
    assert items[3] == {"type": "function_call_output", "call_id": "t1", "output": 'ERROR: {"rows":[]}'}
    assert items[4] == {"role": "user", "content": "reminder"}
    assert params["tools"][1] == {"type": "function", "name": "submit_answer", "description": "final",
                                  "parameters": {"type": "object", "properties": {}}, "strict": True}
