"""FileStore streaming sinks — build item 2 (C5).

Streaming sinks open one file, accept chunks over time, finalize on
close, and emit :class:`StreamStarted` / :class:`StreamEnded` lifecycle
events around it (build item 1b). The durable event log stays
lifecycle-only; per-chunk **frames** fan out ephemerally through the
files catalog daemon (not the event log) so live consumers range-read
the new byte window push-style without flooding the EventStore.

Two write surfaces share this module:

- :meth:`FileStore.open_stream` — direct (the store's own method)
- :func:`litmus.files.stream` — top-level power-user verb

Both return a :class:`StreamingSink` — a context-managed object with
``.write(chunk)`` and ``.close()``. ``close()`` returns the final
``file://`` URI of the artifact.

Format handlers live in a registry mirroring the one-shot
:mod:`litmus.data.files.serializers` registry. Each format defines
``open(path, opts) -> StreamingSink``; format authors implement the
sink protocol per their underlying library.

**Format coverage in v0.2.0:**

- ``raw`` — append-binary; bytes/bytearray/memoryview chunks
- ``jsonl`` — append-text; dict / list / str chunks → one JSON line per
- ``tdms`` — wraps :class:`nptdms.TdmsWriter` (extra: ``[tdms]``)
- ``h5`` — wraps :class:`h5py.File`'s resizable datasets (extra: ``[hdf5]``)

PyAV (mp4 / h264) and soundfile (wav / flac) are explicit follow-ups
(build item 23 — hardware video encoder option pulls in PyAV; audio
arrives with it).

**Live-read-during-write** works for all four formats but with
format-specific caveats. Raw / JSONL are unconditionally
range-readable. TDMS / HDF5 are append-friendly by design but consumer
must reopen the file (or use the library's reload paths) to see new
data; pure HTTP range reads on the bytes don't decode without library
help. See :func:`StreamingSink.byte_offset` for the post-chunk size.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

import orjson

from litmus.data.events import StreamCheckpoint, StreamEnded, StreamStarted
from litmus.data.files.catalog_manager import open_frame_relay

# Optional-extra deps: nptdms (TDMS) and h5py (HDF5) are gated behind
# install extras (``pip install litmus-test[tdms,hdf5]``). The top-level
# try/except lets the module load cleanly without the extra; the
# corresponding sink subclass checks the sentinel and raises a useful
# install hint at construction time. Annotated as ``Any`` so pyright
# doesn't reason about Optional in the success branch.
ChannelObject: Any = None
TdmsWriter: Any = None
_HAS_TDMS = False
try:
    from nptdms import ChannelObject, TdmsWriter  # type: ignore[no-redef]

    _HAS_TDMS = True
except ImportError:
    pass

h5py: Any = None
_HAS_HDF5 = False
try:
    import h5py  # type: ignore[no-redef]

    _HAS_HDF5 = True
except ImportError:
    pass

if TYPE_CHECKING:
    from collections.abc import Callable

    from litmus.data.event_log import EventLog


# --------------------------------------------------------------------- #
# Sink protocol                                                         #
# --------------------------------------------------------------------- #


class StreamingSink(Protocol):
    """Append-only file sink — context-managed.

    Implementations:

    - own the open file handle (or its library equivalent)
    - publish an ephemeral frame notification per :meth:`write` (via the
      files daemon, not the event log)
    - emit :class:`StreamEnded` exactly once on :meth:`close` (and
      tolerate repeated :meth:`close` calls — idempotent)

    Sinks are reusable as context managers via :func:`contextlib.closing`
    semantics — calling :meth:`close` from a ``with`` block is the
    intended pattern.
    """

    @property
    def stream_id(self) -> UUID: ...

    @property
    def uri(self) -> str:
        """The ``file://`` URI for this stream's artifact (stable from open)."""
        ...

    @property
    def byte_offset(self) -> int:
        """Total bytes appended so far. Live consumers range-read
        ``[prev_offset, byte_offset)`` after each frame notification."""
        ...

    def write(self, chunk: Any) -> int:
        """Append ``chunk`` and publish an ephemeral frame notification.

        Returns the number of bytes written (or 0 if the underlying
        library tracks size internally and the sink can't measure it
        per-chunk; e.g. TDMS / HDF5).
        """
        ...

    def close(self) -> str:
        """Finalize the artifact; return the ``file://`` URI."""
        ...

    def __enter__(self) -> StreamingSink: ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


# --------------------------------------------------------------------- #
# Format registry                                                       #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class StreamFormat:
    """One entry in the streaming-sink registry.

    Mirrors :class:`litmus.data.files.serializers.Serializer` but for
    the streaming side. ``extension`` and ``mime`` shape the on-disk
    filename + sidecar metadata; ``open`` is the constructor that
    accepts a resolved path + per-format options and returns a sink.
    """

    extension: str
    mime: str
    open: Callable[..., StreamingSink]
    # True for format writers that need a seekable local file (TDMS/HDF5):
    # they stage locally and publish on close. False (raw/jsonl) writes
    # straight to the backend output stream (local file / S3 multipart).
    needs_local_path: bool = False


_FORMAT_REGISTRY: dict[str, StreamFormat] = {}


def register_format(name: str, fmt: StreamFormat) -> None:
    """Register or override a streaming format handler.

    Called at module import time for built-ins; user code can call
    this to add custom formats. Last-registered wins (no error on
    override — matches the one-shot serializer-registry behavior).
    """
    _FORMAT_REGISTRY[name] = fmt


def get_format(name: str) -> StreamFormat:
    """Look up a registered format; raise with a helpful message if absent."""
    try:
        return _FORMAT_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_FORMAT_REGISTRY)) or "<none registered>"
        raise ValueError(
            f"litmus.files.stream: unknown format {name!r}. "
            f"Known formats: {known}. Register a custom format with "
            "litmus.data.files.streaming.register_format(name, fmt)."
        ) from None


def registered_formats() -> list[str]:
    """Return the sorted list of registered format names — for diagnostics."""
    return sorted(_FORMAT_REGISTRY)


# --------------------------------------------------------------------- #
# Base sink with shared event-emission machinery                        #
# --------------------------------------------------------------------- #


class _BaseSink:
    """Shared open / close / event-emission scaffolding.

    Format-specific sinks subclass this, override :meth:`write` (which
    appends a chunk and returns bytes-written) and optionally hand a
    ``finalizer`` that runs once before :class:`StreamEnded` emits.
    All emit-side bookkeeping — stream_id, StreamStarted / StreamEnded
    — lives here.

    Stream events are **lifecycle-only**. No per-chunk event flood;
    live consumers subscribe to the stream directly (the file on disk,
    Flight for ChannelStore data) and use EventStore only for
    discovery. ``byte_offset`` is an informational producer-side
    property so the caller can know how much has been written; it
    isn't broadcast over the event log.

    ``event_log`` may be ``None``; in that case the sink writes
    silently. Production paths always have one; tests sometimes don't.
    """

    def __init__(
        self,
        *,
        uri: str,
        files_dir: Path,
        name: str,
        format_name: str,
        session_id: str,
        event_log: EventLog | None,
        run_id: UUID | None = None,
        checkpoint_cadence: float | None = None,
    ) -> None:
        self._uri = uri
        self._files_dir = files_dir
        self._name = name
        self._format_name = format_name
        self._session_id_str = session_id
        self._event_log = event_log
        self._run_id = run_id
        self._stream_id = uuid4()
        self._byte_offset = 0
        self._closed = False
        # Stream liveness checkpoint: when set, the write path emits one
        # ``StreamCheckpoint`` (carrying byte_offset) per ``checkpoint_cadence``
        # so a long active file stream renews the session lease instead of going
        # silent on the spine. ``_last_spine_emit`` (monotonic) tracks the sink's
        # most recent event-log emission — StreamStarted / checkpoint reset it.
        self._checkpoint_cadence = checkpoint_cadence
        self._last_spine_emit: float | None = None
        # Resolve the live frame fan-out ONCE (not per chunk). ``None`` when no
        # catalog daemon serves this dir — the writer's hot path then skips all
        # frame work. When a daemon is up, frames go to a non-blocking background
        # relay so the per-chunk do_put never blocks the writer. Coalescing
        # tuning defaults from ``FileOptions`` (the single home for those knobs).
        self._relay = open_frame_relay(files_dir)
        # Emit StreamStarted at construction (NOT at first write) so
        # the ``.uri`` property — valid from construction onward — is
        # never returned for a stream that hasn't announced itself to
        # the event log. Without this, ``observe(name, sink)`` could
        # latch a URI to the vector before any StreamStarted event
        # fired, breaking timeline ordering for subscribers.
        self._emit_started()

    @property
    def stream_id(self) -> UUID:
        return self._stream_id

    @property
    def byte_offset(self) -> int:
        return self._byte_offset

    @property
    def uri(self) -> str:
        """The ``file://`` URI for this stream's artifact.

        Stable from open through close — the sink is constructed with its
        final (collision-resolved) URI, so it's known immediately.
        Satisfies the :class:`~litmus.data.ref.Latchable` protocol —
        :meth:`Context.observe` checks for this property and stamps
        the URI without re-writing when handed a sink:

        ::

            with litmus.files.stream("capture", format="raw") as sink:
                sink.write(chunk)
                observe("daq", sink)   # latches sink.uri on out_*
        """
        return self._uri

    def __enter__(self) -> _BaseSink:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        self.close()

    def write(self, chunk: Any) -> int:  # pragma: no cover — subclass override
        del chunk
        raise NotImplementedError

    def close(self) -> str:  # pragma: no cover — subclass override
        raise NotImplementedError

    # ----- emit helpers --------------------------------------------------

    def _session_uuid(self) -> UUID:
        """Resolve the session_id string to a UUID for event emission.

        Production callers pass canonical UUID strings; tests may pass
        short opaque ids (e.g. ``"test-abc123"``). Try ``UUID(...)``
        first; on failure, derive a deterministic UUID5 from the
        string so tests aren't forced to use full UUIDs to exercise
        the streaming sink.
        """
        try:
            return UUID(self._session_id_str)
        except ValueError:
            return uuid5(NAMESPACE_OID, f"litmus-session-stub:{self._session_id_str}")

    def _emit_started(self) -> None:
        if self._event_log is None:
            return
        self._event_log.emit(
            StreamStarted(
                session_id=self._session_uuid(),
                run_id=self._run_id,
                stream_id=self._stream_id,
                name=self._name,
                format=self._format_name,
            )
        )
        self._last_spine_emit = time.monotonic()

    def _maybe_checkpoint(self) -> None:
        """Emit a ``StreamCheckpoint`` if a cadence of writing has elapsed since
        the sink's last spine event — one per cadence, carrying byte_offset, so a
        long active stream renews the session lease. No-op without a cadence/log."""
        if self._checkpoint_cadence is None or self._event_log is None:
            return
        now = time.monotonic()
        if (
            self._last_spine_emit is not None
            and now - self._last_spine_emit < self._checkpoint_cadence
        ):
            return
        self._event_log.emit(
            StreamCheckpoint(
                session_id=self._session_uuid(),
                run_id=self._run_id,
                uri=self._uri,
                offset=self._byte_offset,
            )
        )
        self._last_spine_emit = now

    def _track_bytes(self, length: int, payload: bytes | None = None) -> None:
        """Advance the byte offset + push an ephemeral frame to subscribers.

        The durable event log stays lifecycle-only (StreamStarted /
        StreamEnded) — no per-chunk event. Each chunk instead fans out a
        non-persisted frame via the files daemon carrying the new bytes
        (``payload``) so a live consumer receives them push-style (req 5) —
        never range-reading a still-growing object, which a remote backend
        cannot serve. Best-effort + non-spawning: a no-op when no daemon
        runs. ``payload`` is ``None`` for format sinks (tdms/h5) whose bytes
        aren't decodable mid-container; a subscriber reloads at the boundary.
        """
        if length <= 0:
            return
        prev = self._byte_offset
        self._byte_offset += length
        if self._relay is not None:
            self._relay.publish(
                {
                    "stream_id": str(self._stream_id),
                    "uri": self._uri,
                    "byte_offset": prev,
                    "length": length,
                    "payload": payload,
                }
            )
        self._maybe_checkpoint()

    def _emit_ended(self, uri: str) -> None:
        # Flush + stop the frame relay before announcing the stream closed, so a
        # subscriber sees every frame before StreamEnded.
        if self._relay is not None:
            self._relay.close()
            self._relay = None
        if self._event_log is None:
            return
        size_bytes = self._byte_offset if self._byte_offset > 0 else None
        self._event_log.emit(
            StreamEnded(
                session_id=self._session_uuid(),
                run_id=self._run_id,
                stream_id=self._stream_id,
                uri=uri,
                size_bytes=size_bytes,
            )
        )


# --------------------------------------------------------------------- #
# Raw bytes sink (zero deps)                                            #
# --------------------------------------------------------------------- #


class _RawByteSink(_BaseSink):
    """Append-binary sink for raw byte chunks.

    Use for: continuous DAQ where the caller does its own framing,
    SCPI session captures, fixture-instrument bridge traffic, any
    byte stream that doesn't need a format library.

    Accepts ``bytes``, ``bytearray``, ``memoryview``. Rejects ``str``
    explicitly (loud) — encoding is the caller's call.
    """

    def __init__(
        self,
        *,
        stream: Any,
        uri: str,
        files_dir: Path,
        name: str,
        format_name: str,
        session_id: str,
        event_log: EventLog | None,
        run_id: UUID | None = None,
        finalizer: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            uri=uri,
            files_dir=files_dir,
            name=name,
            format_name=format_name,
            session_id=session_id,
            event_log=event_log,
            run_id=run_id,
        )
        # Backend output stream (local file / S3 multipart); close publishes.
        self._file: Any = stream
        self._finalizer = finalizer

    def write(self, chunk: Any) -> int:
        if self._closed or self._file is None:
            raise RuntimeError("StreamingSink is closed")
        if isinstance(chunk, str):
            raise TypeError(
                "litmus.files.stream(format='raw'): str chunk rejected. "
                "Encode to bytes (e.g. chunk.encode('utf-8')) — encoding "
                "is the caller's choice for raw streams. Use format='jsonl' "
                "if you want text-line semantics."
            )
        if not isinstance(chunk, bytes | bytearray | memoryview):
            raise TypeError(
                f"litmus.files.stream(format='raw'): chunk must be bytes/"
                f"bytearray/memoryview, got {type(chunk).__name__}."
            )
        data = bytes(chunk)
        self._file.write(data)
        self._file.flush()
        self._track_bytes(len(data), payload=data)
        return len(data)

    def close(self) -> str:
        if self._closed:
            return self.uri
        if self._file is not None:
            # Closing the backend stream publishes the object (S3 multipart
            # completes here) — must precede the finalizer's sidecar + catalog.
            self._file.close()
            self._file = None
        self._closed = True
        if self._finalizer is not None:
            self._finalizer()
        self._emit_ended(self.uri)
        return self.uri


# --------------------------------------------------------------------- #
# JSONL sink (zero deps — uses already-present orjson)                  #
# --------------------------------------------------------------------- #


class _JsonlSink(_BaseSink):
    """Append-text sink — one JSON value per line.

    Use for: structured event logs, line-delimited diagnostic streams,
    anything CLI / log-tail readable.

    Accepts ``dict``, ``list``, ``str``, ``int``, ``float``, ``bool``,
    ``None``. Each :meth:`write` appends one JSON line + trailing
    newline. Encoding is UTF-8.
    """

    def __init__(
        self,
        *,
        stream: Any,
        uri: str,
        files_dir: Path,
        name: str,
        format_name: str,
        session_id: str,
        event_log: EventLog | None,
        run_id: UUID | None = None,
        finalizer: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            uri=uri,
            files_dir=files_dir,
            name=name,
            format_name=format_name,
            session_id=session_id,
            event_log=event_log,
            run_id=run_id,
        )
        # Backend output stream (local file / S3 multipart); close publishes.
        self._file: Any = stream
        self._finalizer = finalizer

    def write(self, chunk: Any) -> int:
        if self._closed or self._file is None:
            raise RuntimeError("StreamingSink is closed")
        if isinstance(chunk, str):
            line = chunk.encode("utf-8") + b"\n"
        else:
            line = orjson.dumps(chunk) + b"\n"
        self._file.write(line)
        self._file.flush()
        self._track_bytes(len(line), payload=line)
        return len(line)

    def close(self) -> str:
        if self._closed:
            return self.uri
        if self._file is not None:
            self._file.close()
            self._file = None
        self._closed = True
        if self._finalizer is not None:
            self._finalizer()
        self._emit_ended(self.uri)
        return self.uri


# --------------------------------------------------------------------- #
# TDMS sink (extra: [tdms])                                             #
# --------------------------------------------------------------------- #


class _TdmsSink(_BaseSink):
    """Append-friendly TDMS sink via :mod:`nptdms`.

    Each :meth:`write` chunk is a list of :class:`nptdms.ChannelObject`
    instances (or a single one). The TDMS file format is built for
    appended chunks; the writer manages segment indexing internally.

    Per-chunk byte size is tracked by stat()ing the file after each
    flush — :mod:`nptdms`'s public API doesn't surface the appended
    byte count directly.
    """

    def __init__(
        self,
        *,
        path: Path,
        uri: str,
        files_dir: Path,
        name: str,
        format_name: str,
        session_id: str,
        event_log: EventLog | None,
        run_id: UUID | None = None,
        finalizer: Callable[[], None] | None = None,
    ) -> None:
        if not _HAS_TDMS:
            raise ImportError(
                "litmus.files.stream(format='tdms') requires the `npTDMS` "
                "package. Install with: pip install litmus-test[tdms]"
            )

        super().__init__(
            uri=uri,
            files_dir=files_dir,
            name=name,
            format_name=format_name,
            session_id=session_id,
            event_log=event_log,
            run_id=run_id,
        )
        # nptdms needs a seekable local path; we write to the backend staging
        # path and publish on close (the finalizer). TdmsWriter is a context
        # manager but we drive its lifecycle manually (the sink IS the context).
        self._path = path
        self._writer: Any = TdmsWriter(str(path))
        self._writer.__enter__()
        self._finalizer = finalizer

    def write(self, chunk: Any) -> int:
        if self._closed or self._writer is None:
            raise RuntimeError("StreamingSink is closed")
        # Accept one ChannelObject or a list of them
        objects = chunk if isinstance(chunk, list | tuple) else [chunk]
        for obj in objects:
            if not isinstance(obj, ChannelObject):
                raise TypeError(
                    "litmus.files.stream(format='tdms'): chunks must be "
                    "nptdms.ChannelObject (or a list of them). Got "
                    f"{type(obj).__name__}. Example: "
                    "ChannelObject('group', 'channel', np.array([...]))."
                )
        self._writer.write_segment(list(objects))
        # Per-chunk byte size: re-stat after write (nptdms doesn't surface it).
        # The frame reports the [prev, size_after) range; payload is None
        # (a partial TDMS container isn't decodable by raw byte slice — a
        # subscriber reloads via nptdms at the segment boundary).
        size_after = self._path.stat().st_size if self._path.exists() else 0
        delta = size_after - self._byte_offset
        self._track_bytes(delta, payload=None)
        return max(delta, 0)

    def close(self) -> str:
        if self._closed:
            return self.uri
        if self._writer is not None:
            self._writer.__exit__(None, None, None)
            self._writer = None
        self._closed = True
        if self._finalizer is not None:
            self._finalizer()
        self._emit_ended(self.uri)
        return self.uri


# --------------------------------------------------------------------- #
# HDF5 sink (extra: [hdf5])                                             #
# --------------------------------------------------------------------- #


class _H5Sink(_BaseSink):
    """Append to resizable HDF5 datasets via :mod:`h5py`.

    Each :meth:`write` chunk is a dict ``{dataset_name: array}``. On
    first chunk, datasets are created with ``maxshape=(None, *rest)``
    (resizable along axis 0). Subsequent chunks append along that
    axis; shape per axis>0 must stay constant.

    HDF5 buffers internally; the sink flushes after every write so
    consumers reopening the file see fresh data, but per-chunk
    byte-delta tracking is the same stat() pattern as TDMS.
    """

    def __init__(
        self,
        *,
        path: Path,
        uri: str,
        files_dir: Path,
        name: str,
        format_name: str,
        session_id: str,
        event_log: EventLog | None,
        run_id: UUID | None = None,
        finalizer: Callable[[], None] | None = None,
    ) -> None:
        if not _HAS_HDF5:
            raise ImportError(
                "litmus.files.stream(format='h5') requires the `h5py` "
                "package. Install with: pip install litmus-test[hdf5]"
            )

        super().__init__(
            uri=uri,
            files_dir=files_dir,
            name=name,
            format_name=format_name,
            session_id=session_id,
            event_log=event_log,
            run_id=run_id,
        )
        # h5py needs a seekable local path (random access); write to the
        # backend staging path and publish on close (the finalizer).
        self._path = path
        self._h5py = h5py
        self._file: Any = h5py.File(str(path), "w")
        self._finalizer = finalizer

    def write(self, chunk: Any) -> int:
        if self._closed or self._file is None:
            raise RuntimeError("StreamingSink is closed")
        if not isinstance(chunk, dict):
            raise TypeError(
                "litmus.files.stream(format='h5'): chunk must be a dict "
                f"of {{dataset_name: array}}. Got {type(chunk).__name__}."
            )
        # numpy import deferred — adds ~150ms to module load otherwise,
        # and h5py write is the only consumer in this file.
        import numpy as np  # noqa: PLC0415

        for dataset_name, value in chunk.items():
            arr = np.asarray(value)
            if dataset_name not in self._file:
                # Create with resizable axis-0
                maxshape = (None, *arr.shape[1:])
                self._file.create_dataset(
                    dataset_name,
                    data=arr,
                    maxshape=maxshape,
                    chunks=True,
                )
            else:
                ds = self._file[dataset_name]
                old_len = ds.shape[0]
                new_len = old_len + arr.shape[0]
                ds.resize((new_len, *ds.shape[1:]))
                ds[old_len:new_len] = arr

        self._file.flush()
        # h5py buffers; re-stat for the appended-byte range. payload is None
        # (a partial HDF5 file isn't decodable by raw slice — a subscriber
        # reloads via h5py at the boundary).
        size_after = self._path.stat().st_size if self._path.exists() else 0
        delta = size_after - self._byte_offset
        self._track_bytes(delta, payload=None)
        return max(delta, 0)

    def close(self) -> str:
        if self._closed:
            return self.uri
        if self._file is not None:
            self._file.close()
            self._file = None
        self._closed = True
        if self._finalizer is not None:
            self._finalizer()
        self._emit_ended(self.uri)
        return self.uri


# --------------------------------------------------------------------- #
# Built-in registry                                                     #
# --------------------------------------------------------------------- #


register_format(
    "raw",
    StreamFormat(
        extension=".bin",
        mime="application/octet-stream",
        open=_RawByteSink,
    ),
)

register_format(
    "jsonl",
    StreamFormat(
        extension=".jsonl",
        mime="application/x-ndjson",
        open=_JsonlSink,
    ),
)

register_format(
    "tdms",
    StreamFormat(
        extension=".tdms",
        mime="application/vnd.ni.tdms",
        open=_TdmsSink,
        needs_local_path=True,
    ),
)

register_format(
    "h5",
    StreamFormat(
        extension=".h5",
        mime="application/x-hdf5",
        open=_H5Sink,
        needs_local_path=True,
    ),
)
