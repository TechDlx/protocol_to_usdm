"""Structured JSON-lines logging with secret redaction applied to every record."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Catch key-shaped strings even when they are not the configured value (e.g. a pasted key).
_KEY_PATTERNS = [re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")]
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
REDACTED = "***REDACTED***"


class Redactor:
    def __init__(self, secrets: list[str]) -> None:
        # Very short values would redact innocent text; real keys are long.
        self._secrets = sorted((s for s in secrets if len(s) >= 8), key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern in _KEY_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text


class JsonFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redact = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Anything passed via `extra=` becomes a top-level field.
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return self._redact(json.dumps(payload, default=str, ensure_ascii=False))


def configure_logging(level: str, secrets: list[str]) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(Redactor(secrets)))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Route uvicorn's own loggers through the same redacting formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers[:] = []
        uv_logger.propagate = True
