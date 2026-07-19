"""``testerkit.files.stream`` power-user verb — C5 integration.

Tests the top-level verb shape (the stub from C3b is now real):

- ``with testerkit.files.stream(name, format=...) as sink:`` yields a
  working :class:`StreamingSink`
- session_id resolves from the active Context when not passed
- session_id arg overrides the Context's
- event_log is pulled from the active logger when present
- Sinks silently skip event emission outside a logger context
- All four formats work end-to-end via the top-level verb

Sibling of ``tests/test_data/test_filestore_streaming.py`` (which
tests the FileStore.open_stream class method directly).

Per CLAUDE.md test conventions: monkeypatches data-dir resolution to
tmp_path so writes stay isolated and the daemon is not touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import orjson
import pytest

import testerkit.files
from testerkit.data.events import FileEnded
from testerkit.data.files import _reset_for_tests, get_filestore
from testerkit.execution._state import (
    get_current_run_scope,
    push_current_context,
    reset_current_context,
    set_current_run_scope,
)


class CollectingLog:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class FakeLogger:
    """Stand-in logger exposing ``event_log`` + ``test_run.id``."""

    def __init__(self, event_log: CollectingLog, run_id: Any = None) -> None:
        self.event_log = event_log

        class _TestRun:
            id = run_id

        self.test_run = _TestRun() if run_id is not None else None


class FakeContext:
    """Minimal Context-shaped object with a ``_session_id`` attribute."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id


@pytest.fixture
def _isolated_filestore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Bind the FileStore singleton to tmp_path."""
    from testerkit.data.files import store as store_module

    monkeypatch.setattr(store_module, "resolve_data_dir", lambda _=None: tmp_path)
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def _logger_slot() -> Iterator[None]:
    """Save + restore the active run-scope ContextVar around a test."""
    prior = get_current_run_scope()
    yield
    set_current_run_scope(prior)


# --------------------------------------------------------------------- #
# Session-id resolution                                                 #
# --------------------------------------------------------------------- #


class TestSessionIdResolution:
    def test_explicit_session_id_wins(self, _isolated_filestore: None) -> None:
        sid = str(uuid4())
        with testerkit.files.stream("explicit", format="raw", session_id=sid) as sink:
            sink.write(b"x")
        assert sid in sink.uri

    def test_resolves_from_active_context(self, _isolated_filestore: None) -> None:
        sid = str(uuid4())
        ctx = FakeContext(sid)
        token = push_current_context(ctx)  # type: ignore[arg-type]
        try:
            with testerkit.files.stream("from_ctx", format="raw") as sink:
                sink.write(b"x")
            assert sid in sink.uri
        finally:
            reset_current_context(token)

    def test_raises_without_session_or_context(self, _isolated_filestore: None) -> None:
        # Pytest's ``context`` fixture pushes a Context onto the
        # ContextVar via the autouse ``_testerkit_push_params``. Clear
        # the var explicitly to exercise the "no context" error path.
        from testerkit.execution._state import _current_context_var  # noqa: PLC0415

        token = _current_context_var.set(None)
        try:
            with pytest.raises(RuntimeError, match="No active session_id"):
                with testerkit.files.stream("orphan", format="raw") as _:
                    pass
        finally:
            _current_context_var.reset(token)


# --------------------------------------------------------------------- #
# Event-log resolution                                                  #
# --------------------------------------------------------------------- #


class TestEventLogResolution:
    def test_pulls_event_log_from_active_logger(
        self, _isolated_filestore: None, _logger_slot: None
    ) -> None:
        sid = str(uuid4())
        run_id = uuid4()
        log = CollectingLog()
        logger = FakeLogger(log, run_id=run_id)
        set_current_run_scope(logger)  # type: ignore[arg-type]

        with testerkit.files.stream("with_log", format="raw", session_id=sid) as sink:
            sink.write(b"abc")
        del sink  # quiet ruff F841 — context-managed by `with`

        # Lifecycle-only: exactly the two events, in order.
        types = [type(e).__name__ for e in log.events]
        assert types == ["FileStarted", "FileEnded"]

        for event in log.events:
            assert event.run_id == run_id
            assert str(event.session_id) == sid

    def test_no_logger_means_silent_writes(self, _isolated_filestore: None) -> None:
        """A test_run-less logger or no logger emits nothing — but still writes."""
        sid = str(uuid4())
        # Make sure no logger is active by NOT pushing one
        with testerkit.files.stream("silent", format="raw", session_id=sid) as sink:
            sink.write(b"abc")
        # The artifact reads back through the store.
        assert get_filestore().read(sink.uri) == b"abc"


# --------------------------------------------------------------------- #
# Format coverage via the top-level verb                                #
# --------------------------------------------------------------------- #


class TestFormatsViaVerb:
    def test_raw(self, _isolated_filestore: None) -> None:
        sid = str(uuid4())
        with testerkit.files.stream("daq", format="raw", session_id=sid) as sink:
            sink.write(b"hello")
            sink.write(b"-world")
        assert get_filestore().read(sink.uri) == b"hello-world"

    def test_jsonl(self, _isolated_filestore: None) -> None:
        sid = str(uuid4())
        with testerkit.files.stream("events", format="jsonl", session_id=sid) as sink:
            sink.write({"a": 1})
            sink.write({"b": 2})
        lines = (get_filestore().read(sink.uri) or b"").splitlines()
        assert orjson.loads(lines[0]) == {"a": 1}
        assert orjson.loads(lines[1]) == {"b": 2}

    def test_tdms(self, _isolated_filestore: None, tmp_path: Path) -> None:
        nptdms = pytest.importorskip("nptdms")
        np = pytest.importorskip("numpy")

        sid = str(uuid4())
        with testerkit.files.stream("capture", format="tdms", session_id=sid) as sink:
            sink.write(nptdms.ChannelObject("daq", "ch1", np.array([1.0, 2.0])))

        # tdms needs a seekable local path; the local backend published it there.
        files = list(tmp_path.glob(f"files/*/{sid}/capture.tdms"))
        assert len(files) == 1
        with nptdms.TdmsFile.open(str(files[0])) as tf:
            assert list(tf["daq"]["ch1"][:]) == [1.0, 2.0]

    def test_h5(self, _isolated_filestore: None, tmp_path: Path) -> None:
        h5py = pytest.importorskip("h5py")
        np = pytest.importorskip("numpy")

        sid = str(uuid4())
        with testerkit.files.stream("capture", format="h5", session_id=sid) as sink:
            sink.write({"v": np.array([1.0, 2.0, 3.0])})

        files = list(tmp_path.glob(f"files/*/{sid}/capture.h5"))
        assert len(files) == 1
        with h5py.File(str(files[0]), "r") as f:
            assert list(f["v"][:]) == [1.0, 2.0, 3.0]


# --------------------------------------------------------------------- #
# Context-manager close-on-exit                                         #
# --------------------------------------------------------------------- #


class TestCloseOnContextExit:
    def test_close_called_even_on_exception(
        self, _isolated_filestore: None, _logger_slot: None
    ) -> None:
        sid = str(uuid4())
        log = CollectingLog()
        logger = FakeLogger(log)
        set_current_run_scope(logger)  # type: ignore[arg-type]

        captured_sink: Any = None
        with pytest.raises(ValueError):
            with testerkit.files.stream("crash", format="raw", session_id=sid) as sink:
                captured_sink = sink
                sink.write(b"partial")
                raise ValueError("boom")
        # Sink still got closed → FileEnded emitted
        ended = [e for e in log.events if isinstance(e, FileEnded)]
        assert len(ended) == 1
        # And the published artifact holds the partial bytes
        assert get_filestore().read(captured_sink.uri) == b"partial"

    def test_double_exit_does_not_emit_twice(
        self, _isolated_filestore: None, _logger_slot: None
    ) -> None:
        sid = str(uuid4())
        log = CollectingLog()
        logger = FakeLogger(log)
        set_current_run_scope(logger)  # type: ignore[arg-type]

        with testerkit.files.stream("double", format="raw", session_id=sid) as sink:
            sink.write(b"x")
        # Idempotent close — direct call after context exit
        sink.close()
        ended = [e for e in log.events if isinstance(e, FileEnded)]
        assert len(ended) == 1


# --------------------------------------------------------------------- #
# Live-read window via events                                           #
# --------------------------------------------------------------------- #


class TestStreamReadbackAndLifecycle:
    """Lifecycle-only event model: the durable log carries exactly
    FileStarted + FileEnded; live consumers receive each chunk
    push-style via ephemeral frames (the files daemon, not the event
    log). The closed artifact reads back whole through the store."""

    def test_artifact_reads_back_and_events_are_lifecycle_only(
        self, _isolated_filestore: None, _logger_slot: None
    ) -> None:
        sid = str(uuid4())
        log = CollectingLog()
        logger = FakeLogger(log)
        set_current_run_scope(logger)  # type: ignore[arg-type]

        with testerkit.files.stream("live", format="raw", session_id=sid) as sink:
            sink.write(b"first-")
            sink.write(b"second-")
            sink.write(b"third")

        assert get_filestore().read(sink.uri) == b"first-second-third"

        # Lifecycle-only — exactly two events, no per-chunk noise
        types = [type(e).__name__ for e in log.events]
        assert types == ["FileStarted", "FileEnded"]

        ended = log.events[-1]
        assert isinstance(ended, FileEnded)
        assert ended.size_bytes == 18
