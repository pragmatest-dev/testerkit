"""Observe handle latching — design doc §4.

When ``observe(name, value)`` is handed something that's already in a
store, it should **stamp the existing URI without re-writing**. Three
latching paths:

1. **URI string** — ``"channel://..."`` or ``"file://..."``
2. **Channel sink handle** — :class:`~testerkit.channels._ChannelSink`
3. **Stream sink handle** — :class:`~testerkit.data.files.streaming._BaseSink`

Both sink classes expose a ``.uri`` property satisfying the
:class:`~testerkit.data.ref.Latchable` Protocol. ``Context.observe``
checks for these before falling through to shape-based dispatch — so
URIs don't get pickled as scalars and sinks don't get re-written as
blobs.

The fourth §4 case — ``Path``-already-in-FileStore — isn't covered
here; FileStore doesn't hand out Paths today (write returns URI
strings), so the case is rare. Booked separately if a user asks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from testerkit import channels, files
from testerkit.data.channels.store import ChannelStore
from testerkit.data.event_log import EventLog
from testerkit.data.ref import Latchable, is_ref
from testerkit.execution._state import (
    push_current_context,
    reset_current_context,
    set_channel_store,
)
from testerkit.execution.harness import Context, TestHarness


@pytest.fixture
def session(tmp_path: Path):
    """Real Context + ChannelStore + FileStore wiring, isolated to tmp_path."""
    from testerkit.data.files import _reset_for_tests as _reset_filestore
    from testerkit.data.files import store as fstore_module

    session_id = uuid4()
    event_log = EventLog(log_dir=tmp_path / "events", session_id=session_id)

    cstore = ChannelStore(tmp_path, session_id, flush_threshold=1000, event_log=event_log)
    cstore.open()

    orig_resolve = fstore_module.resolve_data_dir
    fstore_module.resolve_data_dir = lambda _=None: tmp_path
    _reset_filestore()

    # No RunScope — the bare Context path tests are simpler.
    harness = TestHarness(session_id=session_id, channel_store=cstore)
    ctx = Context(harness=harness, channel_store=cstore, session_id=session_id)

    set_channel_store(cstore)
    token = push_current_context(ctx)

    class _Session:
        pass

    sess = _Session()
    sess.ctx = ctx  # type: ignore[attr-defined]
    sess.channel_store = cstore  # type: ignore[attr-defined]
    sess.session_id = session_id  # type: ignore[attr-defined]
    sess.tmp_path = tmp_path  # type: ignore[attr-defined]

    try:
        yield sess
    finally:
        reset_current_context(token)
        set_channel_store(None)
        cstore.close()
        event_log.close()
        fstore_module.resolve_data_dir = orig_resolve
        _reset_filestore()


# --------------------------------------------------------------------- #
# Latchable Protocol + is_ref helper                                     #
# --------------------------------------------------------------------- #


class TestLatchableProtocol:
    def test_channel_sink_satisfies_protocol(self, session: Any) -> None:
        with channels.stream("dmm.voltage") as sink:
            assert isinstance(sink, Latchable)
            assert isinstance(sink.uri, str)
            assert sink.uri.startswith("channel://dmm.voltage")

    def test_stream_sink_satisfies_protocol(self, session: Any) -> None:
        with files.stream(
            "daq_capture",
            format="raw",
            session_id=str(session.session_id),
        ) as sink:
            assert isinstance(sink, Latchable)
            assert isinstance(sink.uri, str)
            assert sink.uri.endswith(f"/{session.session_id}/daq_capture.bin")

    def test_plain_scalar_does_not_satisfy_protocol(self) -> None:
        assert not isinstance(3.31, Latchable)
        assert not isinstance("just a string", Latchable)
        assert not isinstance([1, 2, 3], Latchable)
        assert not isinstance(b"bytes", Latchable)

    def test_is_ref_recognizes_both_schemes(self) -> None:
        assert is_ref("channel://scope.ch1?session=abc")
        assert is_ref("file://abc/capture.bin")
        # Negative cases
        assert not is_ref("just a string")
        assert not is_ref("https://example.com")
        assert not is_ref(3.14)
        assert not is_ref(None)
        assert not is_ref(b"file://bytes-not-str")


# --------------------------------------------------------------------- #
# URI-string latching                                                    #
# --------------------------------------------------------------------- #


class TestUriStringLatching:
    def test_channel_uri_stamped_verbatim(self, session: Any) -> None:
        uri = "channel://scope.ch1?session=existing-session"
        session.ctx.observe("scope.cap", uri)
        assert session.ctx._observations["scope.cap"] == uri

    def test_file_uri_stamped_verbatim(self, session: Any) -> None:
        uri = "file://some-session/artifact.bin"
        session.ctx.observe("scope.cap", uri)
        assert session.ctx._observations["scope.cap"] == uri

    def test_uri_string_does_not_re_write_to_filestore(self, session: Any) -> None:
        """A URI is already a reference; observe must not re-write it as
        a blob into FileStore. Pre-latching this fell through to the
        scalar-stash path (works by accident); post-latching it's an
        explicit branch — same outcome, intentional now."""
        uri = "file://some-session/artifact.bin"
        session.ctx.observe("scope.cap", uri)

        # No file was created in FileStore for this latched URI
        files_root = session.tmp_path / "files"
        if files_root.exists():
            for date_dir in files_root.iterdir():
                for sid_dir in date_dir.iterdir():
                    # Nothing was written under any session dir
                    contents = list(sid_dir.iterdir())
                    artifact_names = {p.name for p in contents}
                    assert "scope.cap.bin" not in artifact_names


# --------------------------------------------------------------------- #
# Handle latching: channel sink                                          #
# --------------------------------------------------------------------- #


class TestChannelSinkLatching:
    def test_observe_channel_sink_stamps_uri(self, session: Any) -> None:
        with channels.stream("dmm.voltage") as sink:
            sink.write(3.31)
            session.ctx.observe("voltage_capture", sink)
        # URI stamped on out_*; matches sink.uri
        observed = session.ctx._observations["voltage_capture"]
        assert observed.startswith("channel://dmm.voltage")
        assert f"session={session.session_id}" in observed

    def test_observe_channel_sink_does_not_re_write_to_channelstore(self, session: Any) -> None:
        """Handing a sink to observe must NOT trigger another
        ChannelStore.write — the sink already wrote its samples."""
        with channels.stream("dmm.voltage") as sink:
            sink.write(3.31)
            sink.write(3.32)

            # Snapshot row count before observe
            before = session.channel_store.query("dmm.voltage")
            row_count_before = before.num_rows

            session.ctx.observe("capture", sink)

            after = session.channel_store.query("dmm.voltage")
            assert after.num_rows == row_count_before, (
                "observe(sink) should latch the URI, not re-write to ChannelStore"
            )


# --------------------------------------------------------------------- #
# Handle latching: file stream sink                                      #
# --------------------------------------------------------------------- #


class TestStreamSinkLatching:
    def test_observe_stream_sink_stamps_file_uri(self, session: Any) -> None:
        with files.stream(
            "daq_capture",
            format="raw",
            session_id=str(session.session_id),
        ) as sink:
            sink.write(b"some-bytes")
            session.ctx.observe("capture", sink)

        observed = session.ctx._observations["capture"]
        assert observed.endswith(f"/{session.session_id}/daq_capture.bin")

    def test_observe_stream_sink_does_not_re_write_artifact(self, session: Any) -> None:
        """A second copy of the artifact would land at e.g. capture_2.bin
        with the collision suffix. Latching means no second write."""
        with files.stream(
            "daq_capture",
            format="raw",
            session_id=str(session.session_id),
        ) as sink:
            sink.write(b"some-bytes")
            session.ctx.observe("capture", sink)

        # Walk the FileStore tree: exactly one artifact with this name
        # (no ``daq_capture_2.bin`` collision suffix).
        files_root = session.tmp_path / "files"
        artifact_names: list[str] = []
        for date_dir in files_root.iterdir():
            for sid_dir in date_dir.iterdir():
                if sid_dir.name != str(session.session_id):
                    continue
                for p in sid_dir.iterdir():
                    if p.name.startswith("daq_capture") and p.suffix == ".bin":
                        artifact_names.append(p.name)
        assert artifact_names == ["daq_capture.bin"], (
            f"Expected exactly one artifact; got {artifact_names}"
        )


# --------------------------------------------------------------------- #
# Regression: non-latchable values still route through shape dispatch    #
# --------------------------------------------------------------------- #


class TestNonLatchableStillRoutes:
    def test_python_list_still_routes_to_channelstore(self, session: Any) -> None:
        session.ctx.observe("scope.array", [1.0, 2.0, 3.0])
        observed = session.ctx._observations["scope.array"]
        assert observed.startswith("channel://scope.array")

    def test_bytes_blob_still_routes_to_filestore(self, session: Any) -> None:
        session.ctx.observe("screenshot", b"\x89PNG-bytes")
        observed = session.ctx._observations["screenshot"]
        assert observed.startswith("file://")

    def test_scalar_still_stashes_inline(self, session: Any) -> None:
        session.ctx.observe("temp", 23.5)
        assert session.ctx._observations["temp"] == 23.5
