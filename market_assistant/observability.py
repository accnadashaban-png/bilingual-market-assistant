"""Structured JSON logs with a request id, plus Prometheus metrics."""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar

from prometheus_client import Counter, Histogram

request_id: ContextVar[str] = ContextVar("request_id", default="-")

REQUESTS = Counter("assistant_requests_total", "Answered messages", ["route", "language"])
TOOL_CALLS = Counter("assistant_tool_calls_total", "Tool calls", ["tool", "status"])
BLOCKED = Counter("assistant_blocked_queries_total", "SQL rejected by the safety layers")
LATENCY = Histogram("assistant_answer_seconds", "Time to answer a message",
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)), "level": record.levelname,
                 "logger": record.name, "msg": record.getMessage(), "request_id": request_id.get()}
        entry.update(getattr(record, "extra_fields", {}))
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def log(logger: logging.Logger, msg: str, **fields: object) -> None:
    logger.info(msg, extra={"extra_fields": fields})
