"""
tests/test_logging.py — The one-machine-readable-line-per-record contract.

Render ships stdout as text, so this app's debuggability rests entirely on those lines being JSON with a
request_id on every one of them. Three things are worth pinning:

  * the shape — stable keys plus whatever structured extras a call site passed, and a traceback that stays
    inside one line's `exception` field instead of exploding into twenty unparsed lines;
  * the id's life cycle — bound per request, echoed to the client, reset afterwards, and bounded in length;
  * that configuring it twice does not double-log, which is what a --reload worker restart would otherwise
    do to the signal-to-noise ratio.
"""

from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from app.core import logging as app_logging


def record(message: str = "hello", *, level: int = logging.INFO, name: str = "app.test", **extras):
    entry = logging.LogRecord(name, level, "path/file.py", 12, message, None, None)
    for key, value in extras.items():
        setattr(entry, key, value)
    return entry


def parse(line: str) -> dict:
    assert line.endswith("}") and "\n" not in line, "one record must be exactly one line"
    return json.loads(line)


# ── JsonFormatter ──────────────────────────────────────────────────────────────


def test_a_record_becomes_one_json_line_with_the_stable_keys():
    line = app_logging.JsonFormatter().format(record("chat turn completed", name="app.routers.chat"))

    payload = parse(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.routers.chat"
    assert payload["message"] == "chat turn completed"
    assert payload["request_id"] == "-"  # nothing bound outside a request
    assert "ts" in payload


def test_structured_extras_are_carried_through():
    """The access log and the chat router add keys the formatter has never heard of; they must survive."""
    payload = parse(
        app_logging.JsonFormatter().format(record("request", method="POST", path="/api/chat", status=200, duration_ms=12.5))
    )

    assert payload["method"] == "POST"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 12.5


def test_a_logging_exception_stays_inside_one_line():
    """A multi-line traceback in stdout is what turns a log viewer into a puzzle: json escapes the newlines
    so the whole trace rides in one record's exception field."""
    try:
        raise ValueError("boom")
    except ValueError:
        entry = record("agent failed")
        entry.exc_info = sys.exc_info()
        line = app_logging.JsonFormatter().format(entry)

    payload = parse(line)

    assert "ValueError: boom" in payload["exception"]
    assert "Traceback" in payload["exception"]


def test_python_objects_that_are_not_json_serialisable_are_stringified_not_dropped():
    payload = parse(app_logging.JsonFormatter().format(record("turn", conversation_id=object())))

    assert "object object at" in payload["conversation_id"]


def test_non_ascii_text_survives_the_formatter():
    """ensure_ascii would turn a French document title into \\u00c3 noise in the log dump."""
    payload = parse(app_logging.JsonFormatter().format(record("citation from café.pdf")))

    assert payload["message"] == "citation from café.pdf"


def test_internal_log_record_fields_are_not_copied_into_the_output():
    payload = parse(app_logging.JsonFormatter().format(record("request")))

    for internal in ("msg", "args", "pathname", "lineno", "thread", "created"):
        assert internal not in payload


# ── request id life cycle ──────────────────────────────────────────────────────


def test_an_empty_header_becomes_a_generated_id():
    token, request_id = app_logging.bind_request_id(None)
    try:
        assert app_logging.current_request_id() == request_id
        assert len(request_id) == 12
    finally:
        app_logging.reset_request_id(token)


@pytest.mark.parametrize("supplied", ["", "   ", "x" * 200])
def test_a_client_supplied_id_is_bounded_but_never_empty(supplied):
    token, request_id = app_logging.bind_request_id(supplied)
    try:
        assert 0 < len(request_id) <= app_logging.MAX_REQUEST_ID_LENGTH
    finally:
        app_logging.reset_request_id(token)


def test_the_id_resets_when_the_request_ends():
    """A leaked id would tag the next unrelated request with somebody else's trace."""
    token, _ = app_logging.bind_request_id("abc123")
    app_logging.reset_request_id(token)

    assert app_logging.current_request_id() == "-"


def test_the_filter_tags_every_record_with_the_current_request():
    token, request_id = app_logging.bind_request_id("cafe000001")
    try:
        entry = record("retrieved 6 chunks")
        app_logging.RequestIdFilter().filter(entry)
        payload = parse(app_logging.JsonFormatter().format(entry))
    finally:
        app_logging.reset_request_id(token)

    assert payload["request_id"] == "cafe000001"


# ── configure_logging ──────────────────────────────────────────────────────────


_MUTED = ("sentence_transformers", "urllib3", "httpx", "httpcore", "pymongo", "uvicorn.access")


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_noisy = {name: logging.getLogger(name).level for name in _MUTED}
    yield
    root.handlers.clear()
    root.handlers.extend(saved_handlers)
    root.setLevel(saved_level)
    for name, level in saved_noisy.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.parametrize(
    "production,expected_level",
    [(True, logging.INFO), (False, logging.DEBUG)],
)
def test_the_root_level_follows_the_environment(production, expected_level, restore_logging):
    """DEBUG locally, INFO in production: the verbose lines earn their keep only where a developer reads
    them one request at a time."""
    app_logging.configure_logging(production=production)

    assert logging.getLogger().level == expected_level


def test_configuring_twice_does_not_double_the_stream(restore_logging):
    """uvicorn --reload re-runs the composition root; a second handler would print every line twice."""
    app_logging.configure_logging(production=True)
    app_logging.configure_logging(production=True)

    assert len(logging.getLogger().handlers) == 1


def test_the_third_party_chatter_is_muted(restore_logging):
    app_logging.configure_logging(production=False)

    for noisy in _MUTED:
        assert logging.getLogger(noisy).level >= logging.WARNING


def test_json_reaches_stdout(restore_logging):
    """Render collects stdout; a handler on stderr would look like an empty log."""
    handler = app_logging.configure_logging(production=True)
    buffer = io.StringIO()
    handler.setStream(buffer)

    logging.getLogger("app.main").info("startup complete")

    assert parse(buffer.getvalue().strip())["message"] == "startup complete"
