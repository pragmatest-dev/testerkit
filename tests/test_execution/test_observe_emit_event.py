"""Context.observe emits an Observation event — item 4.

Pre-item-4: ``Context.observe(key, value)`` stashed the value in
``_observations`` but was silent on the event timeline. Subscribers
couldn't see captures.

After item 4: each ``observe()`` call results in exactly one
``Observation`` event in the EventStore. Step/vector context is pulled
from active ContextVars at emit time.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from litmus.data.events import Observation
from litmus.data.files import _reset_for_tests
from litmus.execution._state import (
    push_current_step,
    push_current_vector,
    reset_current_step,
    reset_current_vector,
)
from litmus.execution.harness import Context, TestHarness

# --------------------------------------------------------------------- #
# helpers — minimal fakes for the logger / event_log machinery          #
# --------------------------------------------------------------------- #


class FakeEventLog:
    """Captures emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class FakeTestRun:
    id: UUID


class FakeLogger:
    """Minimal stand-in for ``RunScope`` for the ContextVar."""

    def __init__(self, event_log: FakeEventLog | None, run_id: UUID | None = None) -> None:
        self.event_log = event_log
        self.test_run = FakeTestRun(id=run_id) if run_id is not None else None


@pytest.fixture(autouse=True)
def _reset_filestore_singleton() -> None:
    _reset_for_tests()


@pytest.fixture
def event_log() -> FakeEventLog:
    return FakeEventLog()


@pytest.fixture
def session_id() -> UUID:
    return uuid4()


@pytest.fixture
def run_id() -> UUID:
    return uuid4()


@pytest.fixture
def context_with_logger(
    event_log: FakeEventLog, session_id: UUID, run_id: UUID
) -> Iterator[Context]:
    """A Context wired up so observe() can emit events.

    Sets a FakeLogger as the current logger ContextVar so
    ``get_current_logger()`` returns it; resets on teardown.
    """
    harness = TestHarness(session_id=session_id)
    ctx = Context(harness=harness)
    logger = FakeLogger(event_log=event_log, run_id=run_id)
    import litmus.execution.harness as harness_mod

    monkeypatch_target = "get_current_logger"
    original = getattr(harness_mod, monkeypatch_target)
    setattr(harness_mod, monkeypatch_target, lambda: logger)
    try:
        yield ctx
    finally:
        setattr(harness_mod, monkeypatch_target, original)


# --------------------------------------------------------------------- #
# event emission per observe() call                                     #
# --------------------------------------------------------------------- #


def test_observe_scalar_emits_observation_event(
    context_with_logger: Context, event_log: FakeEventLog, session_id: UUID, run_id: UUID
) -> None:
    ctx = context_with_logger
    ctx.observe("temp", 23.5)

    assert len(event_log.events) == 1
    ev = event_log.events[0]
    assert isinstance(ev, Observation)
    assert ev.name == "temp"
    assert ev.value == 23.5
    assert ev.session_id == session_id
    assert ev.run_id == run_id


def test_observe_blob_emits_observation_with_uri_value(
    context_with_logger: Context, event_log: FakeEventLog
) -> None:
    """Observation event carries the URI, not the raw bytes."""
    ctx = context_with_logger
    ctx.observe("payload", b"\xde\xad\xbe\xef")

    assert len(event_log.events) == 1
    ev = event_log.events[0]
    assert isinstance(ev, Observation)
    assert ev.name == "payload"
    assert isinstance(ev.value, str), f"expected URI string, got {type(ev.value)}"
    assert ev.value.startswith("file://"), ev.value


def test_observe_none_value_still_emits(
    context_with_logger: Context, event_log: FakeEventLog
) -> None:
    """None is a legitimate observation; emits with value=None."""
    ctx = context_with_logger
    ctx.observe("nothing", None)

    assert len(event_log.events) == 1
    ev = event_log.events[0]
    assert ev.name == "nothing"
    assert ev.value is None


def test_observe_string_scalar_emits_correctly(
    context_with_logger: Context, event_log: FakeEventLog
) -> None:
    ctx = context_with_logger
    ctx.observe("operator", "ALICE")

    assert len(event_log.events) == 1
    assert event_log.events[0].value == "ALICE"


def test_multiple_observes_emit_multiple_events(
    context_with_logger: Context, event_log: FakeEventLog
) -> None:
    ctx = context_with_logger
    ctx.observe("a", 1.0)
    ctx.observe("b", "two")
    ctx.observe("c", False)

    assert len(event_log.events) == 3
    assert [ev.name for ev in event_log.events] == ["a", "b", "c"]
    assert [ev.value for ev in event_log.events] == [1.0, "two", False]


# --------------------------------------------------------------------- #
# silent skip cases                                                     #
# --------------------------------------------------------------------- #


def test_observe_without_logger_is_silent_but_still_stashes() -> None:
    """No active logger → no event emitted; _observations still populated."""
    ctx = Context(harness=TestHarness(session_id=uuid4()))
    # No push_current_logger — get_current_logger returns None

    ctx.observe("temp", 23.5)
    # No assertion on events (none to assert against)
    assert ctx._observations["temp"] == 23.5


def test_observe_without_event_log_on_logger_is_silent(
    monkeypatch: pytest.MonkeyPatch, session_id: UUID
) -> None:
    """Logger present but event_log=None → no emit; _observations still populated."""
    import litmus.execution.harness as harness_mod

    ctx = Context(harness=TestHarness(session_id=session_id))
    logger = FakeLogger(event_log=None)
    monkeypatch.setattr(harness_mod, "get_current_logger", lambda: logger)

    ctx.observe("temp", 23.5)

    assert ctx._observations["temp"] == 23.5


def test_observe_without_session_id_is_silent_for_scalar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session_id → no emit. Scalar path: still stashes, no error."""
    import litmus.execution.harness as harness_mod

    ctx = Context()  # no harness, no session_id
    event_log = FakeEventLog()
    logger = FakeLogger(event_log=event_log)
    monkeypatch.setattr(harness_mod, "get_current_logger", lambda: logger)

    ctx.observe("temp", 23.5)

    # No event emitted because session_id is None
    assert len(event_log.events) == 0
    # But scalar still stashes
    assert ctx._observations["temp"] == 23.5


# --------------------------------------------------------------------- #
# step/vector context propagation                                        #
# --------------------------------------------------------------------- #


def test_observe_picks_up_step_context_from_contextvar(
    context_with_logger: Context, event_log: FakeEventLog
) -> None:
    """Active step's name/path/index are pulled into the event."""

    @dataclass
    class FakeStep:
        name: str = "voltage_check"
        step_path: str = "power/output/voltage"
        step_index: int = 2

    token = push_current_step(FakeStep())
    try:
        context_with_logger.observe("vout", 3.3)
    finally:
        reset_current_step(token)

    ev = event_log.events[0]
    assert ev.step_name == "voltage_check"
    assert ev.step_path == "power/output/voltage"
    assert ev.step_index == 2


def test_observe_picks_up_vector_context_from_contextvar(
    context_with_logger: Context, event_log: FakeEventLog
) -> None:
    """Active vector's index/retry are pulled into the event."""

    @dataclass
    class FakeVector:
        index: int = 5
        retry: int = 1

    token = push_current_vector(FakeVector())
    try:
        context_with_logger.observe("vout", 3.3)
    finally:
        reset_current_vector(token)

    ev = event_log.events[0]
    assert ev.vector_index == 5
    assert ev.retry == 1


def test_observe_outside_step_vector_emits_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
    context_with_logger: Context,
    event_log: FakeEventLog,
) -> None:
    """No active step/vector → event has default 0-values for context fields.

    Pytest itself sets the step ContextVar during test execution, so we
    monkeypatch get_current_step/get_current_vector to None for this case.
    """
    import litmus.execution.harness as harness_mod

    monkeypatch.setattr(harness_mod, "get_current_step", lambda: None)
    monkeypatch.setattr(harness_mod, "get_current_vector", lambda: None)

    context_with_logger.observe("ambient", 25.0)

    ev = event_log.events[0]
    assert ev.step_name == ""
    assert ev.step_index == 0
    assert ev.step_path == ""
    assert ev.vector_index == 0
    assert ev.retry == 0


# --------------------------------------------------------------------- #
# regression: _observations still populated                              #
# --------------------------------------------------------------------- #


def test_observe_still_populates_observations_dict(
    context_with_logger: Context,
) -> None:
    """Item 4 adds events; _observations dict behavior unchanged."""
    ctx = context_with_logger
    ctx.observe("temp", 23.5)
    ctx.observe("operator", "ALICE")
    ctx.observe("fault", False)
    ctx.observe("blob", b"\x00\x01")

    assert ctx._observations["temp"] == 23.5
    assert ctx._observations["operator"] == "ALICE"
    assert ctx._observations["fault"] is False
    assert ctx._observations["blob"].startswith("file://")
