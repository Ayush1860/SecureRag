"""Structured logging: one JSON object per line, with the current request id on every record.

The API middleware sets ``request_id_var``; the filter copies it onto each record, so logs from
the retriever, the LLM router or the audit writer can all be joined on ``request_id``.
``LOG_FORMAT=text`` gives a human-readable format for local development.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "request_id"}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        extras = {k: v for k, v in vars(record).items() if k not in _STD_ATTRS and not k.startswith("_")}
        if extras:
            payload["extra"] = extras
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(fmt: str = "json", level: str = "INFO") -> None:
    """Install a single stdout handler on the root logger (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_securerag", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler._securerag = True  # type: ignore[attr-defined]
    handler.addFilter(RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn's access log duplicates our request logging; keep its errors only.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
