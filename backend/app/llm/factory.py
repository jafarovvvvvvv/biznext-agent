from __future__ import annotations

from app.config import SUPPORTED_LLM_PROVIDERS, Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.openai_compatible_provider import OpenAICompatibleProvider


def build_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {', '.join(SUPPORTED_LLM_PROVIDERS)}, got '{provider}'")
    common = dict(
        api_key=settings.llm_api_key_value,
        model=settings.llm_model,
        effort=settings.llm_effort,
        timeout_s=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    if provider == "anthropic":
        return AnthropicProvider(fallbacks=settings.llm_fallbacks, **common)
    base_url = settings.llm_base_url if provider == "openai_compatible" else ""
    return OpenAICompatibleProvider(name=provider, base_url=base_url, **common)
