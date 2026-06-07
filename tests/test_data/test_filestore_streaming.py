"""Build item 2 + 1b — FileStore streaming sink + Stream events.

Tests:

- ``FileStore.open_stream`` opens a sink and emits :class:`StreamStarted`.
- :meth:`StreamingSink.write` appends without emitting per-chunk events
  (lifecycle-only event model — see ``test_emits_lifecycle_events_only``).
- :meth:`StreamingSink.close` finalizes + emits :class:`StreamEnded`
  with the final ``file://`` URI + ``size_bytes``.
- Context-manager exit calls close exactly once (idempotent ``close``).
- Sidecar metadata (item 1c) lands at close with correct MIME / size.
- Per format: raw, jsonl, tdms, h5.
- Live-read-during-write — consumer subscribes to the file directly,
  using only :class:`StreamStarted` for discovery (the path).

Per CLAUDE.md test conventions: uses ``tmp_path``-backed FileStore
(FileStore writes don't spawn daemons).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import orjson
import pytest

from litmus.data.events import StreamEnded, StreamStarted
from litmus.data.files import FileStore
from litmus.data.files.streaming import (
    StreamFormat,
    get_format,
    register_format,
    registered_formats,
)


def _sid() -> str:
    return f"test-{uuid4().hex[:12]}"


class CollectingLog:
    """Captures emitted events in order for assertion."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(data_dir=tmp_path)


@pytest.fixture
def log() -> CollectingLog:
    return CollectingLog()


# --------------------------------------------------------------------- #
# Format registry                                                       #
# --------------------------------------------------------------------- #


class TestFormatRegistry:
    def test_builtin_formats_registered(self) -> None:
        names = set(registered_formats())
        assert {"raw", "jsonl", "tdms", "h5"} <= names

    def test_raw_format_metadata(self) -> None:
        fmt = get_format("raw")
        assert fmt.extension == ".bin"
        assert fmt.mime == "application/octet-stream"

    def test_jsonl_format_metadata(self) -> None:
        fmt = get_format("jsonl")
        assert fmt.extension == ".jsonl"
        assert fmt.mime == "application/x-ndjson"

    def test_tdms_format_metadata(self) -> None:
        fmt = get_format("tdms")
        assert fmt.extension == ".tdms"
        assert fmt.mime == "application/vnd.ni.tdms"

    def test_h5_format_metadata(self) -> None:
        fmt = get_format("h5")
        assert fmt.extension == ".h5"
        assert fmt.mime == "application/x-hdf5"

    def test_unknown_format_raises_with_helpful_message(self) -> None:
        with pytest.raises(ValueError, match="unknown format 'nope'"):
            get_format("nope")

    def test_register_custom_format(self, store: FileStore, log: CollectingLog) -> None:
        """User code can register a format and FileStore picks it up."""

        class _UpperBytesSink:
            """Trivial sink that upper-cases bytes — for registry test."""

            def __init__(
                self,
                *,
                path: Path,
                name: str,
                format_name: str,
                session_id: str,
                event_log: Any,
                run_id: Any = None,
                finalizer: Any = None,
            ) -> None:
                del name, format_name, run_id, event_log
                self.path = path  # exposed per StreamingSink protocol
                self._sid = session_id
                self._file = path.open("ab")
                self._closed = False
                self._finalizer = finalizer
                self.byte_offset = 0
                self.stream_id = uuid4()

            @property
            def uri(self) -> str:
                return f"file://{self._sid}/{self.path.name}"

            def write(self, chunk: bytes) -> int:
                n = self._file.write(chunk.upper())
                self.byte_offset += n
                return n

            def close(self) -> str:
                if not self._closed:
                    self._file.close()
                    self._closed = True
                    if self._finalizer is not None:
                        self._finalizer()
                return f"file://{self._sid}/{self.path.name}"

            def __enter__(self) -> _UpperBytesSink:
                return self

            def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                del exc_type, exc, tb
                self.close()

        register_format(
            "upper",
            StreamFormat(extension=".upper", mime="text/x-upper", open=_UpperBytesSink),
        )

        sid = _sid()
        sink = store.open_stream("greeting", format="upper", session_id=sid, event_log=log)
        sink.write(b"hello")
        sink.close()

        # find file
        files = list((store._files_dir).glob(f"*/{sid}/greeting.upper"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"HELLO"


# --------------------------------------------------------------------- #
# Raw byte sink                                                         #
# --------------------------------------------------------------------- #


class TestRawSink:
    def test_basic_round_trip(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        sink = store.open_stream("daq", format="raw", session_id=sid, event_log=log)
        sink.write(b"abc")
        sink.write(b"defg")
        uri = sink.close()

        assert uri == f"file://{sid}/daq.bin"
        files = list(store._files_dir.glob(f"*/{sid}/daq.bin"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"abcdefg"

    def test_emits_lifecycle_events_only(self, store: FileStore, log: CollectingLog) -> None:
        """Lifecycle-only: StreamStarted + StreamEnded. No per-chunk events.

        Per the Position-2 split for channels — the EventStore is for
        discovery (what streams are open / done), not per-write notifications.
        Live consumers subscribe to the stream directly (range-read,
        format-library decode, Flight). Verified here by writing multiple
        chunks and asserting the event log only carries the two lifecycle
        events regardless of chunk count.
        """
        sid = _sid()
        with store.open_stream("daq", format="raw", session_id=sid, event_log=log) as sink:
            sink.write(b"abc")
            sink.write(b"defg")
            sink.write(b"more")
            sink.write(b"chunks")

        types = [type(e).__name__ for e in log.events]
        assert types == ["StreamStarted", "StreamEnded"]

        started = log.events[0]
        assert isinstance(started, StreamStarted)
        assert started.stream_id == sink.stream_id
        assert started.name == "daq"
        assert started.format == "raw"
        assert started.path is not None
        assert started.path.endswith("daq.bin")

        ended = log.events[1]
        assert isinstance(ended, StreamEnded)
        assert ended.stream_id == sink.stream_id
        assert ended.uri == f"file://{sid}/daq.bin"
        assert ended.size_bytes == len(b"abc" + b"defg" + b"more" + b"chunks")

    def test_byte_offset_property_tracks_per_write(
        self, store: FileStore, log: CollectingLog
    ) -> None:
        """``sink.byte_offset`` is the producer-side write counter (not an event).

        Producers can query it to know how much they've written; nothing
        is emitted per write.
        """
        sid = _sid()
        with store.open_stream("daq", format="raw", session_id=sid, event_log=log) as sink:
            assert sink.byte_offset == 0
            sink.write(b"abc")
            assert sink.byte_offset == 3
            sink.write(b"defg")
            assert sink.byte_offset == 7

    def test_str_chunk_rejected_with_helpful_error(
        self, store: FileStore, log: CollectingLog
    ) -> None:
        sid = _sid()
        sink = store.open_stream("daq", format="raw", session_id=sid, event_log=log)
        try:
            with pytest.raises(TypeError, match="str chunk rejected"):
                sink.write("text")
        finally:
            sink.close()

    def test_bytearray_and_memoryview_accepted(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        with store.open_stream("daq", format="raw", session_id=sid, event_log=log) as sink:
            sink.write(bytearray(b"abc"))
            sink.write(memoryview(b"def"))

        files = list(store._files_dir.glob(f"*/{sid}/daq.bin"))
        assert files[0].read_bytes() == b"abcdef"

    def test_close_is_idempotent(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        sink = store.open_stream("daq", format="raw", session_id=sid, event_log=log)
        sink.write(b"abc")
        uri1 = sink.close()
        uri2 = sink.close()
        assert uri1 == uri2

        # Only one StreamEnded event despite double close
        ended = [e for e in log.events if isinstance(e, StreamEnded)]
        assert len(ended) == 1

    def test_write_after_close_raises(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        sink = store.open_stream("daq", format="raw", session_id=sid, event_log=log)
        sink.close()
        with pytest.raises(RuntimeError, match="closed"):
            sink.write(b"too late")

    def test_silent_when_no_event_log(self, store: FileStore) -> None:
        """``event_log=None`` writes pass through without emitting (or erroring)."""
        sid = _sid()
        with store.open_stream("daq", format="raw", session_id=sid, event_log=None) as sink:
            sink.write(b"abc")
            sink.write(b"def")

        files = list(store._files_dir.glob(f"*/{sid}/daq.bin"))
        assert files[0].read_bytes() == b"abcdef"

    def test_collision_safe_filenames(self, store: FileStore, log: CollectingLog) -> None:
        """Two streams with the same name in the same session get unique paths."""
        sid = _sid()
        sink1 = store.open_stream("daq", format="raw", session_id=sid, event_log=log)
        sink2 = store.open_stream("daq", format="raw", session_id=sid, event_log=log)
        try:
            assert sink1.path != sink2.path
            assert sink1.path.name == "daq.bin"
            assert sink2.path.name == "daq_2.bin"
        finally:
            sink1.close()
            sink2.close()


# --------------------------------------------------------------------- #
# JSONL sink                                                            #
# --------------------------------------------------------------------- #


class TestJsonlSink:
    def test_dict_chunks_round_trip(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        with store.open_stream("events", format="jsonl", session_id=sid, event_log=log) as sink:
            sink.write({"ts": 1, "v": 3.3})
            sink.write({"ts": 2, "v": 3.4})

        files = list(store._files_dir.glob(f"*/{sid}/events.jsonl"))
        assert len(files) == 1
        lines = files[0].read_bytes().splitlines()
        assert orjson.loads(lines[0]) == {"ts": 1, "v": 3.3}
        assert orjson.loads(lines[1]) == {"ts": 2, "v": 3.4}

    def test_string_chunk_treated_as_pre_serialized_line(
        self, store: FileStore, log: CollectingLog
    ) -> None:
        """String chunks bypass JSON encoding — useful for raw log lines."""
        sid = _sid()
        with store.open_stream("logs", format="jsonl", session_id=sid, event_log=log) as sink:
            sink.write("custom-pre-encoded")

        files = list(store._files_dir.glob(f"*/{sid}/logs.jsonl"))
        assert files[0].read_text() == "custom-pre-encoded\n"

    def test_list_chunk_round_trips(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        with store.open_stream("events", format="jsonl", session_id=sid, event_log=log) as sink:
            sink.write([1, 2, 3])

        files = list(store._files_dir.glob(f"*/{sid}/events.jsonl"))
        line = files[0].read_bytes().splitlines()[0]
        assert orjson.loads(line) == [1, 2, 3]

    def test_byte_offset_grows_per_write(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        with store.open_stream("events", format="jsonl", session_id=sid, event_log=log) as sink:
            sink.write({"a": 1})
            offset_after_first = sink.byte_offset
            sink.write({"b": 2})
            offset_after_second = sink.byte_offset

        assert offset_after_first > 0
        assert offset_after_second > offset_after_first


# --------------------------------------------------------------------- #
# TDMS sink (extra: [tdms])                                             #
# --------------------------------------------------------------------- #


class TestTdmsSink:
    def test_round_trip_with_two_segments(self, store: FileStore, log: CollectingLog) -> None:
        nptdms = pytest.importorskip("nptdms")
        np = pytest.importorskip("numpy")

        sid = _sid()
        with store.open_stream("capture", format="tdms", session_id=sid, event_log=log) as sink:
            sink.write(nptdms.ChannelObject("daq", "ch1", np.array([1.0, 2.0, 3.0])))
            sink.write(nptdms.ChannelObject("daq", "ch1", np.array([4.0, 5.0])))

        files = list(store._files_dir.glob(f"*/{sid}/capture.tdms"))
        assert len(files) == 1
        # Read back via nptdms
        with nptdms.TdmsFile.open(str(files[0])) as tf:
            data = tf["daq"]["ch1"][:]
            assert list(data) == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_list_of_channel_objects_per_write(self, store: FileStore, log: CollectingLog) -> None:
        nptdms = pytest.importorskip("nptdms")
        np = pytest.importorskip("numpy")

        sid = _sid()
        with store.open_stream("capture", format="tdms", session_id=sid, event_log=log) as sink:
            sink.write(
                [
                    nptdms.ChannelObject("daq", "ch1", np.array([1.0, 2.0])),
                    nptdms.ChannelObject("daq", "ch2", np.array([10.0, 20.0])),
                ]
            )

        files = list(store._files_dir.glob(f"*/{sid}/capture.tdms"))
        with nptdms.TdmsFile.open(str(files[0])) as tf:
            assert list(tf["daq"]["ch1"][:]) == [1.0, 2.0]
            assert list(tf["daq"]["ch2"][:]) == [10.0, 20.0]

    def test_emits_lifecycle_events(self, store: FileStore, log: CollectingLog) -> None:
        nptdms = pytest.importorskip("nptdms")
        np = pytest.importorskip("numpy")

        sid = _sid()
        with store.open_stream("capture", format="tdms", session_id=sid, event_log=log) as sink:
            sink.write(nptdms.ChannelObject("daq", "ch1", np.array([1.0])))
            sink.write(nptdms.ChannelObject("daq", "ch1", np.array([2.0])))

        types = [type(e).__name__ for e in log.events]
        assert types == ["StreamStarted", "StreamEnded"]

        started = log.events[0]
        assert isinstance(started, StreamStarted)
        assert started.format == "tdms"

        ended = log.events[1]
        assert isinstance(ended, StreamEnded)
        assert ended.stream_id == sink.stream_id
        assert ended.uri == f"file://{sid}/capture.tdms"
        assert ended.size_bytes is not None and ended.size_bytes > 0

    def test_non_channel_object_rejected(self, store: FileStore, log: CollectingLog) -> None:
        pytest.importorskip("nptdms")
        sid = _sid()
        sink = store.open_stream("capture", format="tdms", session_id=sid, event_log=log)
        try:
            with pytest.raises(TypeError, match="ChannelObject"):
                sink.write({"not": "a channel object"})
        finally:
            sink.close()


# --------------------------------------------------------------------- #
# HDF5 sink (extra: [hdf5])                                             #
# --------------------------------------------------------------------- #


class TestH5Sink:
    def test_resizable_dataset_appends(self, store: FileStore, log: CollectingLog) -> None:
        h5py = pytest.importorskip("h5py")
        np = pytest.importorskip("numpy")

        sid = _sid()
        with store.open_stream("capture", format="h5", session_id=sid, event_log=log) as sink:
            sink.write({"voltage": np.array([1.0, 2.0, 3.0])})
            sink.write({"voltage": np.array([4.0, 5.0])})

        files = list(store._files_dir.glob(f"*/{sid}/capture.h5"))
        assert len(files) == 1
        with h5py.File(str(files[0]), "r") as f:
            assert list(f["voltage"][:]) == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_multi_dataset_dict(self, store: FileStore, log: CollectingLog) -> None:
        h5py = pytest.importorskip("h5py")
        np = pytest.importorskip("numpy")

        sid = _sid()
        with store.open_stream("capture", format="h5", session_id=sid, event_log=log) as sink:
            sink.write({"voltage": np.array([1.0, 2.0]), "current": np.array([0.1, 0.2])})
            sink.write({"voltage": np.array([3.0]), "current": np.array([0.3])})

        files = list(store._files_dir.glob(f"*/{sid}/capture.h5"))
        with h5py.File(str(files[0]), "r") as f:
            assert list(f["voltage"][:]) == [1.0, 2.0, 3.0]
            assert list(f["current"][:]) == [0.1, 0.2, 0.3]

    def test_non_dict_chunk_rejected(self, store: FileStore, log: CollectingLog) -> None:
        pytest.importorskip("h5py")
        sid = _sid()
        sink = store.open_stream("capture", format="h5", session_id=sid, event_log=log)
        try:
            with pytest.raises(TypeError, match="dict"):
                sink.write([1, 2, 3])
        finally:
            sink.close()


# --------------------------------------------------------------------- #
# Sidecar (item 1c) — lands at close                                    #
# --------------------------------------------------------------------- #


class TestStreamingSidecar:
    def test_sidecar_lands_at_close_with_correct_mime(
        self, store: FileStore, log: CollectingLog
    ) -> None:
        sid = _sid()
        with store.open_stream("daq", format="raw", session_id=sid, event_log=log) as sink:
            sink.write(b"abcdef")

        meta = store.read_attributes(f"file://{sid}/daq.bin")
        assert meta is not None
        assert meta.mime == "application/octet-stream"
        assert meta.extension == ".bin"
        assert meta.size_bytes == 6
        assert meta.attributes == {}

    def test_user_attributes_persist_to_sidecar(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        with store.open_stream(
            "daq",
            format="raw",
            session_id=sid,
            event_log=log,
            attributes={"sample_rate_hz": 48000, "channel_label": "Ch1"},
        ) as sink:
            sink.write(b"x")

        meta = store.read_attributes(f"file://{sid}/daq.bin")
        assert meta is not None
        assert meta.attributes == {"sample_rate_hz": 48000, "channel_label": "Ch1"}

    def test_sidecar_records_jsonl_mime(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        with store.open_stream("events", format="jsonl", session_id=sid, event_log=log) as sink:
            sink.write({"v": 1})

        meta = store.read_attributes(f"file://{sid}/events.jsonl")
        assert meta is not None
        assert meta.mime == "application/x-ndjson"
        assert meta.extension == ".jsonl"


# --------------------------------------------------------------------- #
# Live-read-during-write protocol                                       #
# --------------------------------------------------------------------- #


class TestLiveReadDuringWrite:
    """A consumer can range-read the file while the producer is still writing.

    Lifecycle-only event model: the producer emits StreamStarted with the
    on-disk path; the consumer subscribes to the stream **directly**
    (range-read the file, stat its size, decode via the format library).
    EventStore is used for *discovery* only — the consumer reads
    StreamStarted to learn the path, then deals with the file.

    This test exercises that pattern with the raw format (the simplest
    case for range-read) — pin the protocol so it can't silently
    regress to per-chunk events.
    """

    def test_consumer_can_read_file_while_producer_is_writing(
        self, store: FileStore, log: CollectingLog
    ) -> None:
        sid = _sid()
        sink = store.open_stream("stream", format="raw", session_id=sid, event_log=log)

        # Consumer learns path from StreamStarted — the one event we emit at open.
        started = next(e for e in log.events if isinstance(e, StreamStarted))
        assert started.path is not None
        consumer_path = Path(started.path)

        # First chunk lands; consumer stat()s the file size and range-reads
        # the new window. No per-chunk event needed.
        prev_offset = 0
        sink.write(b"hello-")
        size = consumer_path.stat().st_size
        with consumer_path.open("rb") as f:
            f.seek(prev_offset)
            assert f.read(size - prev_offset) == b"hello-"
        prev_offset = size

        # Second chunk; same protocol — consumer polls + range-reads.
        sink.write(b"world")
        size = consumer_path.stat().st_size
        with consumer_path.open("rb") as f:
            f.seek(prev_offset)
            assert f.read(size - prev_offset) == b"world"

        # No StreamFrameIndex events were emitted (lifecycle-only).
        non_lifecycle = [e for e in log.events if not isinstance(e, StreamStarted | StreamEnded)]
        assert non_lifecycle == []

        sink.close()


# --------------------------------------------------------------------- #
# vector_id prefix + run_id stamping                                    #
# --------------------------------------------------------------------- #


class TestStreamMetadata:
    def test_vector_id_prefix_in_filename(self, store: FileStore, log: CollectingLog) -> None:
        sid = _sid()
        with store.open_stream(
            "daq",
            format="raw",
            session_id=sid,
            vector_id="abcd1234efgh5678",
            event_log=log,
        ) as sink:
            sink.write(b"x")

        files = list(store._files_dir.glob(f"*/{sid}/abcd1234_daq.bin"))
        assert len(files) == 1

    def test_run_id_stamped_on_events(self, store: FileStore, log: CollectingLog) -> None:
        """run_id propagates through to every Stream* event."""
        sid = str(uuid4())  # real UUID — no derivation
        run_id = uuid4()
        with store.open_stream(
            "daq",
            format="raw",
            session_id=sid,
            event_log=log,
            run_id=run_id,
        ) as sink:
            sink.write(b"x")

        for event in log.events:
            assert event.run_id == run_id
            assert str(event.session_id) == sid
