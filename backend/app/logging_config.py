from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

_DSN_PASSWORD = re.compile(r"(?P<scheme>postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+:)(?P<password>[^@\s]+)@", re.IGNORECASE)
_API_KEY = re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{16,})")
_PASSWORD_KV = re.compile(r"(?i)\b(password|passwd|api_key|apikey|token)=([^\s&]+)")

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def mask_secrets(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    masked = _DSN_PASSWORD.sub(lambda m: f"{m.group('scheme')}***@", text)
    masked = _API_KEY.sub("***", masked)
    masked = _PASSWORD_KV.sub(lambda m: f"{m.group(1)}=***", masked)
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, "***")
    return masked


class JsonFormatter(logging.Formatter):
    """One JSON object per line; structured fields come from `extra=`."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets = tuple(secrets or ())

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info and record.levelno >= logging.ERROR:
            payload["exc"] = self.formatException(record.exc_info)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        return mask_secrets(line, self._secrets)


def configure_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(secrets))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper() if isinstance(level, str) else logging.INFO)
    # sqlglot warns loudly when it falls back to parsing unknown statements as Command
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic", "openai", "psycopg.pool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for uvicorn_logger in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(uvicorn_logger).handlers = []
        logging.getLogger(uvicorn_logger).propagate = True
