"""
core/logging.py — One JSON line per record, every line tagged with the request that caused it.

Why JSON instead of the default text line: Render ships stdout as text, so a human reading it can grep, but
a chat turn that touches four agents, two databases, and a model produces interleaved lines from different
concurrent requests. A `request_id` on every record — and on the response header, so a user can quote it —
is what turns that soup into a trace you can actually follow.

The id travels in a ContextVar rather than being passed around, because the call sites that want it are
twenty stack frames below the middleware that knows it and none of them should have to take a parameter for
logging. ContextVars are copied into the thread Starlette uses for sync generators, so the chat router's
threadpool work keeps the same id.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from typing import Any

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("documind.request_id", default="-")

# Not a secret and not PII, but still bounded: a caller could otherwise stuff a kilobyte into a header and
# have it echoed into every log line and back out in the response.
MAX_REQUEST_ID_LENGTH = 64


def current_request_id() -> str:
    return _request_id.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def bind_request_id(value: str | None) -> tuple[contextvars.Token, str]:
    """Set the id for this request and hand back the token so the middleware can reset it."""
    resolved = (value or "").strip()[:MAX_REQUEST_ID_LENGTH] or new_request_id()
    return _request_id.set(resolved), resolved


def reset_request_id(token: contextvars.Token) -> None:
    _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render the record as a single JSON object.

    Extra fields go through `record.__dict__`, so any handler can add structured keys (the access log adds
    `method`, `path`, `status`) without this formatter knowing them in advance.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        for key, value in vars(record).items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "request_id",
    "message",
    "asctime",
}


class AccessLogMiddleware:
    """Pure ASGI middleware: assign the request id, emit one access line per request.

    Deliberately not `BaseHTTPMiddleware`. That class wraps the response body in its own stream, which is
    exactly the thing you do not want between a chat endpoint and a server-sent-event stream that has to
    flush frame by frame.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.logger = logging.getLogger("documind.access")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header_value = next(
            (value.decode("latin-1") for name, value in scope.get("headers", []) if name == b"x-request-id"),
            None,
        )
        token, request_id = bind_request_id(header_value)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        path = scope.get("path", "")
        # /health is hit by uptime pings; logging it would bury the lines that mean something.
        quiet = path == "/api/health"
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            if not quiet:
                self.logger.info(
                    "request",
                    extra={
                        "method": scope.get("method", "-"),
                        "path": path,
                        "status": status_holder["status"],
                        "duration_ms": duration_ms,
                    },
                )
            reset_request_id(token)


def configure_logging(*, production: bool) -> logging.Handler:
    """Install the JSON handler on the root logger, once, at the composition root.

    Level stays INFO in production: the point of structured logs is to read them after an incident, and a
    WARNING-only stream is the same as silence. uvicorn's own access logger is muted because this module's
    middleware already records every request, with the request id attached and the duration measured.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO if production else logging.DEBUG)

    for noisy in ("sentence_transformers", "urllib3", "httpx", "httpcore", "pymongo"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # uvicorn installs handlers on its own loggers; propagating lets one formatter own the whole stream.
    for uvicorn_logger in ("uvicorn", "uvicorn.error"):
        target = logging.getLogger(uvicorn_logger)
        target.handlers.clear()
        target.propagate = True

    return handler
