from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "BizNext Agent"
APP_VERSION = "0.1.0"

SUPPORTED_LLM_PROVIDERS = ("anthropic", "openai", "openai_compatible")


def _default_semantic_dir() -> str:
    # backend/app/config.py -> repository root /semantic
    return str(Path(__file__).resolve().parents[2] / "semantic")


class Settings(BaseSettings):
    """All runtime configuration comes from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_environment: str = "demo"

    database_url: SecretStr = SecretStr("")
    agent_database_url: SecretStr = SecretStr("")
    data_schema: str = "demo"
    semantic_dir: str = Field(default_factory=_default_semantic_dir)
    app_timezone: str = "Asia/Almaty"

    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_effort: str = "high"
    llm_max_output_tokens: int = 16000
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 2
    llm_fallbacks: str = "auto"

    agent_max_iterations: int = 10
    agent_max_tool_calls: int = 30
    agent_max_sql_retries: int = 3
    agent_max_rows_to_llm: int = 60
    agent_max_result_chars_to_llm: int = 12000
    agent_timeout_seconds: float = 180.0
    agent_history_turns: int = 6

    sql_max_rows: int = 500
    sql_statement_timeout_ms: int = 15000
    sql_max_query_length: int = 20000

    admin_token: SecretStr = SecretStr("")
    insights_cache_seconds: int = 3600
    log_level: str = "INFO"

    @field_validator("llm_provider", "llm_effort", "llm_fallbacks", mode="before")
    @classmethod
    def _normalize_lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("llm_base_url", "llm_model", "data_schema", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @property
    def llm_api_key_value(self) -> str:
        return self.llm_api_key.get_secret_value().strip()

    @property
    def admin_token_value(self) -> str:
        return self.admin_token.get_secret_value().strip()

    def secret_values(self) -> list[str]:
        """Values that must never appear in logs."""
        secrets = [self.llm_api_key_value, self.admin_token_value]
        for url in (self.database_url.get_secret_value(), self.agent_database_url.get_secret_value()):
            password = dsn_password(url)
            if password:
                secrets.append(password)
        return [s for s in secrets if s and len(s) >= 4]


def dsn_password(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return unquote(parts.password) if parts.password else None


def dsn_public_info(url: str) -> dict[str, str | None]:
    """Database name, user and host of a DSN — never the password."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return {"database": None, "user": None, "host": None}
    database = parts.path.lstrip("/") or None
    return {
        "database": unquote(database) if database else None,
        "user": unquote(parts.username) if parts.username else None,
        "host": parts.hostname,
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
