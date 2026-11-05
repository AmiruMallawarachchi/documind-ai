"""
tests/test_llm_retry.py — The backoff wrapper, tested directly against a scripted Groq stub.

Why this file exists separately from the API tests: the agent tests need a model that answers, and these
need a model that fails. Failure is the part with the interesting rules — which statuses are worth another
call, how long the user waits, and the one that is easy to get catastrophically wrong: never restart a
stream that has already shown the user words, because that appends a second answer mid-sentence.

`time.sleep` is monkeypatched throughout, so a test of three backoffs runs in milliseconds instead of
hanging the suite on the real waits.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import llm


class StatusError(Exception):
    """Stands in for a provider error that carries an HTTP status the way the Groq clients do."""

    def __init__(self, status_code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeChat:
    """Returns whatever it is told to, raising on the way if asked to."""

    def __init__(self, *, replies=None, raises=None, stream_chunks=None, stream_raises=None) -> None:
        self.replies = list(replies or [])
        self.raises = list(raises or [])
        self.stream_chunks = stream_chunks
        self.stream_raises = list(stream_raises or [])
        self.invokes = 0
        self.streams = 0

    def invoke(self, messages):
        self.invokes += 1
        if self.raises:
            raise self.raises.pop(0)
        return SimpleNamespace(content=self.replies.pop(0) if self.replies else "")

    def stream(self, messages):
        self.streams += 1
        if self.stream_raises:
            raise self.stream_raises.pop(0)
        for chunk in self.stream_chunks or []:
            yield SimpleNamespace(content=chunk)


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    """Record the backoff waits instead of sleeping through them."""
    waits: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: waits.append(seconds))
    return waits


def use_chat(monkeypatch, chat: FakeChat) -> FakeChat:
    monkeypatch.setattr(llm, "get_chat_model", lambda **kwargs: chat)
    return chat


# ── Which failures are worth retrying ──────────────────────────────────────────


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_transient_statuses_are_retryable(status_code):
    assert llm._retryable(StatusError(status_code)) is True


@pytest.mark.parametrize("status_code", [400, 401, 404])
def test_client_errors_are_not_retried(status_code):
    # Retrying a 400 does not fix the prompt; it spends quota and delays the real error.
    assert llm._retryable(StatusError(status_code)) is False


def test_a_wrapped_provider_error_is_judged_on_its_cause():
    wrapped = RuntimeError("chat completion failed")
    wrapped.__cause__ = StatusError(429)

    assert llm._retryable(wrapped) is True


@pytest.mark.parametrize("text", ["Error: 429 Too Many Requests", "rate limit exceeded", "Model overloaded", "Request timed out"])
def test_status_only_in_the_message_still_counts(text):
    # Some clients surface the reason as text and nothing else; treating those as fatal would turn a
    # momentary Groq throttle into a visible failure.
    assert llm._retryable(RuntimeError(text)) is True


def test_an_unrecognised_failure_is_not_retried():
    assert llm._retryable(ValueError("bad prompt shape")) is False


def test_backoff_grows_then_caps_and_jitters():
    # Each attempt is checked against the band it must fall in, rather than comparing one sample against
    # the next: jitter means a high first draw can legitimately beat a low second draw.
    assert 0.5 <= llm._sleep_before(0) <= 1.0
    assert 1.0 <= llm._sleep_before(1) <= 2.0
    assert 2.0 <= llm._sleep_before(2) <= 4.0
    assert llm._sleep_before(40) <= llm._BACKOFF_CAP_SECONDS
    # Jitter: the same attempt number must not produce the same wait twice in a row, or every worker
    # retries in lockstep and re-creates the overload it is waiting out.
    samples = {llm._sleep_before(2) for _ in range(20)}
    assert len(samples) > 1


# ── complete_text ──────────────────────────────────────────────────────────────


def test_a_rate_limited_call_is_retried_and_its_answer_returned(monkeypatch, no_sleep):
    chat = use_chat(monkeypatch, FakeChat(raises=[StatusError(429)], replies=["VERDICT: PASS"]))

    assert llm.complete_text([]) == "VERDICT: PASS"
    assert chat.invokes == 2
    # One wait, and it is the half-opened first backoff: 0.5-1.0s for attempt 0.
    assert len(no_sleep) == 1
    assert 0.5 <= no_sleep[0] <= 1.0


def test_retries_stop_at_the_attempt_ceiling(monkeypatch, no_sleep):
    chat = use_chat(monkeypatch, FakeChat(raises=[StatusError(429)] * llm._MAX_ATTEMPTS))

    with pytest.raises(llm.LLMUnavailableError, match="unavailable after"):
        llm.complete_text([])

    assert chat.invokes == llm._MAX_ATTEMPTS
    assert len(no_sleep) == llm._MAX_ATTEMPTS - 1


def test_a_bad_request_fails_immediately_without_wasting_quota(monkeypatch, no_sleep):
    chat = use_chat(monkeypatch, FakeChat(raises=[StatusError(400, "context length exceeded")]))

    with pytest.raises(llm.LLMUnavailableError, match="Model call failed"):
        llm.complete_text([])

    assert chat.invokes == 1
    assert no_sleep == []


# ── stream_text ────────────────────────────────────────────────────────────────


def test_a_failure_before_the_first_token_is_retried(monkeypatch, no_sleep):
    chat = use_chat(
        monkeypatch,
        FakeChat(stream_raises=[StatusError(429)], stream_chunks=["The fee is ", "250 dollars"]),
    )

    assert "".join(llm.stream_text([])) == "The fee is 250 dollars"
    assert chat.streams == 2


def test_a_failure_mid_stream_is_not_retried_because_it_would_duplicate_the_answer(monkeypatch, no_sleep):
    """The rule this whole module turns on.

    Once words have reached the user, restarting the call appends a second answer to the first. So a
    stream that dies part-way raises, and the chat router turns it into one in-band error frame.
    """

    class DiesHalfway:
        def __init__(self) -> None:
            self.streams = 0

        def stream(self, messages):
            self.streams += 1

            def body():
                yield SimpleNamespace(content="The fee is ")
                raise StatusError(429)

            return body()

    chat = DiesHalfway()
    monkeypatch.setattr(llm, "get_chat_model", lambda **kwargs: chat)

    collected = []
    with pytest.raises(llm.LLMUnavailableError, match="mid-answer"):
        for piece in llm.stream_text([]):
            collected.append(piece)

    assert collected == ["The fee is "]
    assert chat.streams == 1
    assert no_sleep == []
