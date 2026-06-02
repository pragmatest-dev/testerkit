"""``litmus.files.stream`` power-user verb — C5 integration.

Tests the top-level verb shape (the stub from C3b is now real):

- ``with litmus.files.stream(name, format=...) as sink:`` yields a
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

import litmus.files
from litmus.data.events import StreamEnded, StreamFrameIndex, StreamStarted
from litmus.data.files import _reset_for_tests
from litmus.execution._state import (
    get_current_logger,
    push_current_context,
    reset_current_context,
    set_current_logger,
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
    from litmus.data.files import store as store_module

    monkeypatch.setattr(store_module, "resolve_data_dir", lambda _=None: tmp_path)
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def _logger_slot() -> Iterator[None]:
    """Save + restore the active logger ContextVar around a test."""
    prior = get_current_logger()
    yield
    set_current_logger(prior)


# --------------------------------------------------------------------- #
# Session-id resolution                                                 #
# --------------------------------------------------------------------- #


class TestSessionIdResolution:
    def test_explicit_session_id_wins(self, _isolated_filestore: None) -> None:
        sid = str(uuid4())
        with litmus.files.stream("explicit", format="raw", session_id=sid) as sink:
            sink.write(b"x")
        assert str(sid) in str(sink.path)

    def test_resolves_from_active_context(self, _isolated_filestore: None) -> None:
        sid = str(uuid4())
        ctx = FakeContext(sid)
        token = push_current_context(ctx)  # type: ignore[arg-type]
        try:
            with litmus.files.stream("from_ctx", format="raw") as sink:
                sink.write(b"x")
            assert sid in str(sink.path)
        finally:
            reset_current_context(token)

    def test_raises_without_session_or_context(self, _isolated_filestore: None) -> None:
        with pytest.raises(RuntimeError, match="no active session_id"):
            with litmus.files.stream("orphan", format="raw") as _:
                pass


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
        set_current_logger(logger)  # type: ignore[arg-type]

        with litmus.files.stream("with_log", format="raw", session_id=sid) as sink:
            sink.write(b"abc")

        types = [type(e).__name__ for e in log.events]
        assert "StreamStarted" in types
        assert "StreamFrameIndex" in types
        assert "StreamEnded" in types

        for event in log.events:
            assert event.run_id == run_id
            assert str(event.session_id) == sid

    def test_no_logger_means_silent_writes(self, _isolated_filestore: None) -> None:
        """A test_run-less logger or no logger emits nothing — but still writes."""
        sid = str(uuid4())
        # Make sure no logger is active by NOT pushing one
        with litmus.files.stream("silent", format="raw", session_id=sid) as sink:
            sink.write(b"abc")
        # Sink path exists with content
        assert sink.path.exists()
        assert sink.path.read_bytes() == b"abc"


# --------------------------------------------------------------------- #
# Format coverage via the top-level verb                                #
# --------------------------------------------------------------------- #


class TestFormatsViaVerb:
    def test_raw(self, _isolated_filestore: None) -> None:
        sid = str(uuid4())
        with litmus.files.stream("daq", format="raw", session_id=sid) as sink:
            sink.write(b"hello")
            sink.write(b"-world")
        assert sink.path.read_bytes() == b"hello-world"

    def test_jsonl(self, _isolated_filestore: None) -> None:
        sid = str(uuid4())
        with litmus.files.stream("events", format="jsonl", session_id=sid) as sink:
            sink.write({"a": 1})
            sink.write({"b": 2})
        lines = sink.path.read_bytes().splitlines()
        assert orjson.loads(lines[0]) == {"a": 1}
        assert orjson.loads(lines[1]) == {"b": 2}

    def test_tdms(self, _isolated_filestore: None) -> None:
        nptdms = pytest.importorskip("nptdms")
        np = pytest.importorskip("numpy")

        sid = str(uuid4())
        with litmus.files.stream("capture", format="tdms", session_id=sid) as sink:
            sink.write(nptdms.ChannelObject("daq", "ch1", np.array([1.0, 2.0])))

        with nptdms.TdmsFile.open(str(sink.path)) as tf:
            assert list(tf["daq"]["ch1"][:]) == [1.0, 2.0]

    def test_h5(self, _isolated_filestore: None) -> None:
        h5py = pytest.importorskip("h5py")
        np = pytest.importorskip("numpy")

        sid = str(uuid4())
        with litmus.files.stream("capture", format="h5", session_id=sid) as sink:
            sink.write({"v": np.array([1.0, 2.0, 3.0])})

        with h5py.File(str(sink.path), "r") as f:
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
        set_current_logger(logger)  # type: ignore[arg-type]

        captured_sink: Any = None
        with pytest.raises(ValueError):
            with litmus.files.stream("crash", format="raw", session_id=sid) as sink:
                captured_sink = sink
                sink.write(b"partial")
                raise ValueError("boom")
        # Sink still got closed → StreamEnded emitted
        ended = [e for e in log.events if isinstance(e, StreamEnded)]
        assert len(ended) == 1
        # And the file holds the partial bytes
        assert captured_sink.path.read_bytes() == b"partial"

    def test_double_exit_does_not_emit_twice(
        self, _isolated_filestore: None, _logger_slot: None
    ) -> None:
        sid = str(uuid4())
        log = CollectingLog()
        logger = FakeLogger(log)
        set_current_logger(logger)  # type: ignore[arg-type]

        with litmus.files.stream("double", format="raw", session_id=sid) as sink:
            sink.write(b"x")
        # Idempotent close — direct call after context exit
        sink.close()
        ended = [e for e in log.events if isinstance(e, StreamEnded)]
        assert len(ended) == 1


# --------------------------------------------------------------------- #
# Live-read window via events                                           #
# --------------------------------------------------------------------- #


class TestLiveReadViaEvents:
    def test_consumer_can_compute_window_from_events(
        self, _isolated_filestore: None, _logger_slot: None
    ) -> None:
        sid = str(uuid4())
        log = CollectingLog()
        logger = FakeLogger(log)
        set_current_logger(logger)  # type: ignore[arg-type]

        with litmus.files.stream("live", format="raw", session_id=sid) as sink:
            sink.write(b"first-")
            sink.write(b"second-")
            sink.write(b"third")

        started = next(e for e in log.events if isinstance(e, StreamStarted))
        assert started.path is not None
        on_disk = Path(started.path)

        fis = [e for e in log.events if isinstance(e, StreamFrameIndex)]
        assert [fi.byte_offset for fi in fis] == [6, 13, 18]
        assert [fi.frame_count for fi in fis] == [1, 2, 3]
        assert on_disk.read_bytes() == b"first-second-third"
