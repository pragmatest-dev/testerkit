"""Channel store — streaming Arrow IPC with live subscriptions.

Producers write directly via write(). Stores data in per-channel Arrow IPC
files with flexible per-channel schemas inferred from the first write.
Supports live in-process subscriptions via on_channel().
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import warnings
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import duckdb
import pyarrow as pa
import pyarrow.flight as flight
import pyarrow.ipc as ipc

from litmus.data._ipc_writer import BufferedIPCWriter
from litmus.data.channels import flight_manager
from litmus.data.channels.models import (
    SCALAR_SCHEMA,
    ChannelDescriptor,
    ChannelSample,
    _data_type_for,
    _infer_schema,
    batch_row_to_sample,
    encode_value,
    sample_to_batch,
)
from litmus.data.events import ChannelClosed, ChannelStarted
from litmus.data.ref import classify_value, make_channel_uri

_WRITE_ERRORS = (OSError, pa.ArrowException)  # type: ignore[attr-defined]


def _to_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime to UTC (or pass through ``None``).

    Naive datetimes are interpreted as already UTC; aware datetimes
    are converted via ``astimezone(UTC)``.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _lttb_indices(values: Sequence[float], n_out: int) -> list[int]:
    """Largest Triangle Three Buckets downsampling — return selected indices.

    Visually lossless: preserves peaks, valleys, and shape better than naive
    stride decimation. Delegates to ``tsdownsample`` (compiled LTTB); first and
    last points are always kept.

    Reference: Sveinn Steinarsson, "Downsampling Time Series for Visual
    Representation", MSc thesis, University of Iceland, 2013.
    """
    n = len(values)
    if n <= n_out or n_out < 3:
        return list(range(n))
    # Heavy deps deferred off the module import path — only the decimation
    # (query w/ max_points) path pays numpy/tsdownsample's load.
    import numpy as np  # noqa: PLC0415
    from tsdownsample import LTTBDownsampler  # noqa: PLC0415

    indices = LTTBDownsampler().downsample(np.asarray(values, dtype=float), n_out=n_out)
    return [int(i) for i in indices]


def _decimate_table(table: pa.Table, max_points: int) -> pa.Table:
    """Apply LTTB decimation to an Arrow table.

    Uses the ``value`` column for scalar channels, or row index for
    struct/array channels (where there's no single numeric column).
    """
    n = len(table)
    if n <= max_points:
        return table

    # Find best column for LTTB area calculation
    if "value" in table.schema.names:
        col = table.column("value")
        try:
            values = [float(v.as_py()) for v in col]
        except (TypeError, ValueError):
            # Non-numeric value column — fall back to stride
            indices = list(range(0, n, max(1, n // max_points)))[:max_points]
            return table.take(indices)
    else:
        # Struct/array channel — use row index as proxy (preserves time density)
        values = list(range(n))

    indices = _lttb_indices(values, max_points)
    return table.take(indices)


class ChannelEventEmitter(Protocol):
    """Structural type for anything ChannelStore can emit lifecycle events into.

    Both the production :class:`litmus.data.event_log.EventLog` and the
    test-side ``CollectingLog`` shape (just an ``emit`` method) satisfy
    this. Keeps :class:`ChannelStore` decoupled from the heavyweight
    event-log subsystem.
    """

    def emit(self, event: Any) -> None: ...


class _ChannelWriter(BufferedIPCWriter):
    """Manages streaming IPC output for a single channel.

    Extends ``BufferedIPCWriter`` with segment rotation: after each flush
    the current IPC file is closed (making it readable) and the next flush
    writes to a new segment file.
    """

    def __init__(
        self,
        channel_id: str,
        data_type: str,
        schema: pa.Schema,
        path: Path,
        flush_threshold: int = 100,
    ) -> None:
        super().__init__(path=path, schema=schema, flush_threshold=flush_threshold)
        self.channel_id = channel_id
        self.data_type = data_type
        # Template: /dir/channel_id_session.arrow → segments append _NNN
        self._path_template = path
        self._segment = 0
        self._closed_paths: list[Path] = []

    @property
    def path(self) -> Path:
        """Current segment path."""
        if self._segment == 0:
            return self._path_template
        stem = self._path_template.stem
        return self._path_template.with_name(f"{stem}_{self._segment:03d}.arrow")

    @property
    def all_paths(self) -> list[Path]:
        """All closed segment paths (readable by ipc.open_stream)."""
        return list(self._closed_paths)

    def _on_flush(self, batch: pa.RecordBatch) -> None:
        """Rotate: close this segment so it's readable, open next on demand."""
        del batch  # rotation logic doesn't read the batch — only the parent's signature requires it
        if self._writer is not None:
            self._closed_paths.append(self.path)
            self._writer.close()
            self._writer = None
            self._segment += 1


class ChannelStore:
    """Streaming Arrow IPC store for instrument channel data.

    Features:
    - Flexible per-channel schemas (inferred from first write)
    - Streaming writes (flush every N rows)
    - Per-channel descriptors (units, role) on the segment schema metadata
    - Live in-process subscriptions via on_channel()
    - Mid-session query() merging flushed files + in-memory buffer
    """

    def __init__(  # noqa: PLR0913
        self,
        data_dir: Path,
        session_id: UUID,
        flush_threshold: int = 100,
        *,
        serve: bool = False,
        host: str = "127.0.0.1",
        port: int = 0,
        event_log: ChannelEventEmitter | None = None,
        index: bool = False,
    ) -> None:
        # Parent-only convention — caller passes the results parent
        # (containing ``runs/``, ``channels/``, ``events/`` …); the
        # store owns its ``channels/`` subdir. Mirrors RunStore /
        # StepsQuery / MeasurementsQuery / EventStore.
        self._channels_dir = data_dir / "channels"
        self._session_id = session_id
        self._flush_threshold = flush_threshold
        self._writers: dict[str, _ChannelWriter] = {}
        self._registry: dict[str, ChannelDescriptor] = {}
        self._subscribers: dict[str, list[Callable[[ChannelSample], None]]] = {}
        self._global_subscribers: list[Callable[[ChannelSample], None]] = []
        # Batch-level taps (whole RecordBatch per received chunk) — the
        # cross-process fan-out registers here so it relays a batch once instead
        # of re-exploding to per-sample deliveries.
        self._batch_subscribers: dict[str, list[Callable[[str, pa.RecordBatch], None]]] = {}
        self._global_batch_subscribers: list[Callable[[str, pa.RecordBatch], None]] = []
        self._serve = serve
        self._flight_host = host
        self._flight_port = port
        self._flight_location: str | None = None
        self._flight_client: flight.FlightClient | None = None
        # Persistent per-channel do_put writers — held open across samples so a
        # high-rate scalar producer pays the Flight stream handshake once per
        # channel, not once per sample. The batch schema is the fixed
        # ``sample_schema()`` (value is always JSON utf8), so a held stream
        # accepts every later sample. Assumes one producer thread per store
        # (the existing single-writer model); not shared across threads.
        self._flight_writers: dict[str, flight.FlightStreamWriter] = {}
        # Position 2 (item 4b): ChannelStore owns ChannelStarted /
        # ChannelClosed emission because per-(channel, session)
        # tracking lives here naturally — the registry already records
        # first write. Any writer path (observer.read /
        # Context.stream / channels.write / FileStore stream sink)
        # gets the right lifecycle events without coordinating its
        # own tracker. ``event_log`` may be ``None`` for tests /
        # bringup paths with no session event log.
        self._event_log = event_log
        # First-write run_id per channel — pairs with ChannelClosed
        # on session-end so the two events carry the same run context.
        # ``None`` for channels written outside any run (daemon writes,
        # interactive bringup).
        self._channel_run_ids: dict[str, UUID | None] = {}
        self._closed = False

        # Warm DuckDB index — daemon-only (Opt 1: the daemon indexes
        # producer files and serves at-rest query from the index; it no
        # longer persists its own segment copy). The index is a derived
        # cache: producer IPC files are the durable truth, so it is built
        # in-memory and rebuilt by scanning closed segments on open().
        # Producers (serve=True writers) never set ``index`` — they only
        # persist + push.
        self._index_enabled = index
        self._index_db: duckdb.DuckDBPyConnection | None = None
        self._index_local = threading.local()
        self._index_lock = threading.Lock()
        self._pending: list[dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._pending_threshold = 100

        # The synchronous daemon push is OFF the write path: write() enqueues the
        # sample and a background thread does the Flight do_put, so capture runs
        # at durable-append speed regardless of daemon/subscriber drain. The live
        # feed drops on overflow (live = from-now); the durable segment is whole.
        # Default on; LITMUS_CHANNELS_SYNC_PUSH=1 forces the inline sync push
        # (A/B benchmarking + rollback).
        self._async_push = os.environ.get("LITMUS_CHANNELS_SYNC_PUSH") != "1"
        self._push_queue: queue.Queue[tuple[str, ChannelSample]] | None = None
        self._push_thread: threading.Thread | None = None
        self._push_stop: threading.Event | None = None
        self._push_drops = 0

    def open(self) -> None:
        self._channels_dir.mkdir(parents=True, exist_ok=True)
        if self._index_enabled:
            self._index_open()
        if self._serve:
            self._connect_or_serve()
            if self._async_push and self._flight_location is not None:
                self._push_queue = queue.Queue(maxsize=10_000)
                self._push_stop = threading.Event()
                self._push_thread = threading.Thread(
                    target=self._push_loop, name="channel-pusher", daemon=True
                )
                self._push_thread.start()

    def list_channel_info(self) -> list[tuple[ChannelDescriptor, pa.Schema]]:
        """Return (descriptor, schema) for each registered channel."""
        result: list[tuple[ChannelDescriptor, pa.Schema]] = []
        for cid, desc in self._registry.items():
            writer = self._writers.get(cid)
            schema = writer.schema if writer else SCALAR_SCHEMA
            result.append((desc, schema))
        return result

    def get_channel_schema(self, channel_id: str) -> pa.Schema | None:
        """Return the Arrow schema for a channel, or None if unknown."""
        writer = self._writers.get(channel_id)
        if writer is not None:
            return writer.schema
        if channel_id in self._registry:
            return SCALAR_SCHEMA
        return None

    def write(  # noqa: PLR0913
        self,
        channel_id: str,
        value: object,
        *,
        units: str | None = None,
        sample_interval: float | None = None,
        source: str = "observe",
        instrument_role: str = "",
        resource: str = "",
        sampled_at: datetime | None = None,
        attributes: dict[str, Any] | None = None,
        run_id: UUID | None = None,
    ) -> str:
        """Write a value directly to a channel.

        Uses flexible schema inference — the first write to a channel
        determines its Arrow schema. Returns a ``channel://`` URI string.

        Args:
            channel_id: User-chosen channel name (e.g. "scope.ch1_waveform").
            value: Scalar, array, dict, string, bool, or numpy array.
            units: Optional unit string.
            sample_interval: For array data, seconds between samples.
            source: Source label for the channel registry.
            instrument_role: Station-config role of the instrument
                producing this channel (e.g. ``"psu"``, ``"dmm"``).
                Stamped on the registry descriptor on first write so
                the channels list shows which instrument owns each
                channel without parsing the channel id.
            resource: Driver connection string (VISA address etc.) of
                the instrument producing this channel. Same purpose
                as ``instrument_role`` — first-write provenance.
            sampled_at: Hardware-side sampling timestamp (build
                item 11). ``None`` if the driver doesn't know; the
                ``received_at`` column always captures the system-side
                write time so analytics has a fallback. Most scope /
                DAQ acquisitions carry a hardware timestamp; simple
                DMM measure calls don't.
            attributes: Channel-level metadata dict (units string is
                redundant if also passed via ``units=``; richer fields
                like coupling, channel name, trigger offset live
                here). Stamped on the registry descriptor on first
                write; subsequent writes' attributes are ignored (the
                channel's identity is fixed once registered). Use the
                first-write site to declare the channel's metadata
                fully.

        Returns:
            ``channel://`` URI pointing to this data in the store.

        Raises:
            ValueError: If value classifies as "blob" (not storable).
        """
        if "/" in channel_id or "\\" in channel_id or ".." in channel_id:
            raise ValueError(
                f"Invalid channel_id '{channel_id}': must not contain path separators or '..'"
            )

        vtype = classify_value(value)
        if vtype == "blob":
            raise ValueError(
                f"Channel {channel_id}: value type {type(value).__name__} is not numeric. "
                "Use file:// refs for non-numeric data."
            )

        now = datetime.now(UTC)

        data_type, row, sample = self._to_arrow_row(
            channel_id,
            value,
            now,
            source,
            units=units,
            sample_interval=sample_interval,
            sampled_at=sampled_at,
        )
        if row is None:
            raise ValueError(f"Channel {channel_id}: could not classify value")

        # Register channel
        if channel_id not in self._registry:
            self._registry[channel_id] = ChannelDescriptor(
                channel_id=channel_id,
                data_type=data_type,
                units=units,
                instrument_role=instrument_role,
                resource=resource,
                attributes=dict(attributes) if attributes else {},
                first_seen=now,
            )
            # Position 2 (item 4b): emit ChannelStarted exactly once per
            # (channel_id, session_id) — coincides with first-time registry
            # entry. Stamp the first-write run_id so the paired
            # ``ChannelClosed`` on session-end carries the same run
            # context.
            self._channel_run_ids[channel_id] = run_id
            if self._event_log is not None:
                self._event_log.emit(
                    ChannelStarted(
                        session_id=self._session_id,
                        run_id=run_id,
                        channel_id=channel_id,
                        units=units,
                        instrument_role=instrument_role or None,
                        method=source or None,
                        resource=resource or None,
                    )
                )

        # Get or create writer (schema inferred from first value)
        if channel_id not in self._writers:
            schema = _infer_schema(self._normalize_value(value, sample_interval))
            # Descriptor rides on the segment's Arrow schema metadata (the
            # daemon reads + serves it); it is metadata, never an indexed column.
            schema = schema.with_metadata(
                {
                    b"litmus.channel_descriptor": self._registry[channel_id]
                    .model_dump_json()
                    .encode()
                }
            )
            session_short = str(self._session_id)[:8]
            today = date.today().isoformat()
            path = self._channels_dir / today / f"{channel_id}_{session_short}.arrow"
            self._writers[channel_id] = _ChannelWriter(
                channel_id,
                data_type,
                schema,
                path,
                self._flush_threshold,
            )

        self._writers[channel_id].append(row)

        # Notify subscribers
        if sample is not None:
            self._notify(channel_id, sample)
            # Co-located batch subscribers (a Flight server wrapping THIS store)
            # get a 1-row batch tap. Guarded: a producer that only pushes to the
            # daemon has no batch subscribers, so capture pays nothing here.
            if self._global_batch_subscribers or self._batch_subscribers.get(channel_id):
                self._notify_batch(channel_id, sample_to_batch(sample))
            # Push to Flight daemon so cross-process subscribers see it.
            # Async-push mode: enqueue (off the write path) and let the
            # background pusher do the do_put; drop on overflow (live = from-now).
            if self._push_queue is not None:
                try:
                    self._push_queue.put_nowait((channel_id, sample))
                except queue.Full:
                    self._push_drops += 1
            else:
                self._flight_push(channel_id, sample)
            # Keep an index-enabled store consistent on any write path
            # (the daemon never calls write(), but in-process index stores do).
            if self._index_enabled:
                self._pending_extend([self._index_row(channel_id, sample)])

        return make_channel_uri(channel_id, str(self._session_id))

    @staticmethod
    def _normalize_value(
        value: object,
        sample_interval: float | None = None,
    ) -> object:
        """Normalize legacy tuple format and numpy arrays for schema inference.

        Build item 11b (C3a-pre): the array payload lands in the
        ``value`` key (uniform with the scalar shape's ``value``
        column). Pre-rename this used ``samples`` (plural); the
        column was asymmetric with scalar rows' ``value`` column for
        no semantic gain — one channel write is one sample / one
        row, regardless of whether its payload is a scalar or an
        array.
        """
        # Legacy waveform tuple: ([items], dt)
        if isinstance(value, (list, tuple)) and len(value) >= 1:
            first = value[0]
            if isinstance(first, (list, tuple)):
                items = list(first)
                dt = float(value[1]) if len(value) > 1 else 0.0
                return {"value": items, "sample_interval": dt}

        # numpy array → list
        tolist = getattr(value, "tolist", None)
        if tolist is not None:
            return {"value": tolist(), "sample_interval": sample_interval or 0.0}

        # Plain non-empty list/tuple → array with sample_interval (any leaf
        # type — bool / int / float / str supported per build item 14).
        if isinstance(value, (list, tuple)) and value:
            return {"value": list(value), "sample_interval": sample_interval or 0.0}

        return value

    def _to_arrow_row(
        self,
        channel_id: str,
        value: object,
        received_at: datetime,
        source: str,
        *,
        units: str | None = None,
        sample_interval: float | None = None,
        sampled_at: datetime | None = None,
    ) -> tuple[str, dict | None, ChannelSample | None]:
        """Convert a value to an Arrow-compatible row dict.

        ``received_at`` (build item 11) is the system-side write time
        — always set. ``sampled_at`` is the optional hardware-side
        sampling time; callers with a hardware-timestamped sample
        pass it through.
        """
        normalized = self._normalize_value(value, sample_interval)

        sid = str(self._session_id)

        common = {
            "received_at": received_at,
            "sampled_at": sampled_at,
            "source_method": source,
            "session_id": sid,
        }

        if isinstance(normalized, dict):
            row: dict = {**common, **normalized}
            # A normalized dict with a ``value`` list is an array
            # write (the result of ``_normalize_value`` folding an
            # array / tuple / numpy ndarray into ``{value,
            # sample_interval}``). Tag it with the typed array form
            # (build item 14: ``"array:<leaf>"``); ``"struct"`` is kept
            # for arbitrary structured records (the dict shape).
            payload = normalized.get("value")
            if isinstance(payload, list) and "sample_interval" in normalized:
                data_type = _data_type_for(payload)
            else:
                data_type = "struct"
            sample_value = normalized
        elif isinstance(normalized, (bool, int, float, str)):
            # Per build item 14, leaf type is preserved on the row's
            # ``value`` column (no more int → float cast). The order
            # ``bool, int, float, str`` matters: ``True`` is also an
            # ``int`` in Python, so the bool branch must come first via
            # the ``isinstance`` tuple ordering plus ``_data_type_for``.
            row = {**common, "value": normalized}
            data_type = _data_type_for(normalized)
            sample_value = normalized
        else:
            warnings.warn(
                f"Channel {channel_id}: cannot store {type(value).__name__}",
                stacklevel=3,
            )
            return "scalar:float", None, None

        sample = ChannelSample(
            channel_id=channel_id,
            received_at=received_at,
            sampled_at=sampled_at,
            value=sample_value,
            units=units,
            sample_interval=sample_interval,
            source_method=source,
            session_id=sid,
        )
        return data_type, row, sample

    def _notify(self, channel_id: str, sample: ChannelSample) -> None:
        """Notify channel and global subscribers.

        Broad ``except Exception`` is deliberate: each user-supplied
        callback runs in isolation so a bug in one subscriber doesn't
        shadow the rest of the fan-out.
        """
        for cb in self._subscribers.get(channel_id, []):
            try:
                cb(sample)
            except Exception as exc:  # noqa: BLE001 — subscriber isolation
                warnings.warn(
                    f"Channel subscriber failed on '{channel_id}': {exc}",
                    stacklevel=2,
                )
        for cb in self._global_subscribers:
            try:
                cb(sample)
            except Exception as exc:  # noqa: BLE001 — subscriber isolation
                warnings.warn(
                    f"Channel subscriber failed on '{channel_id}': {exc}",
                    stacklevel=2,
                )

    def on_channel(
        self,
        channel_id: str | None,
        callback: Callable[[ChannelSample], None],
    ) -> Callable[[], None]:
        """Subscribe to live channel data.

        Args:
            channel_id: Channel to subscribe to, or None for all channels.
            callback: Called with ChannelSample on each new data point.

        Returns:
            Unsubscribe callable.
        """
        if channel_id is None:
            self._global_subscribers.append(callback)

            def unsub() -> None:
                try:
                    self._global_subscribers.remove(callback)
                except ValueError:
                    pass

            return unsub

        self._subscribers.setdefault(channel_id, []).append(callback)

        def unsub() -> None:
            try:
                self._subscribers[channel_id].remove(callback)
            except (ValueError, KeyError):
                pass

        return unsub

    def _notify_batch(self, channel_id: str, batch: pa.RecordBatch) -> None:
        """Deliver a whole received batch to batch-level subscribers (the
        cross-process fan-out), channel-specific then global."""
        for cb in self._batch_subscribers.get(channel_id, []):
            try:
                cb(channel_id, batch)
            except Exception as exc:  # noqa: BLE001 — subscriber isolation
                warnings.warn(
                    f"Channel batch subscriber failed on '{channel_id}': {exc}",
                    stacklevel=2,
                )
        for cb in self._global_batch_subscribers:
            try:
                cb(channel_id, batch)
            except Exception as exc:  # noqa: BLE001 — subscriber isolation
                warnings.warn(
                    f"Channel batch subscriber failed on '{channel_id}': {exc}",
                    stacklevel=2,
                )

    def on_batch(
        self,
        channel_id: str | None,
        callback: Callable[[str, pa.RecordBatch], None],
    ) -> Callable[[], None]:
        """Subscribe to whole received batches (not per-sample).

        ``callback(channel_id, batch)`` fires once per ``ingest_batch`` chunk —
        the fan-out path that relays a batch without re-exploding it to rows.
        ``channel_id=None`` subscribes to all channels.
        """
        if channel_id is None:
            self._global_batch_subscribers.append(callback)

            def unsub_global() -> None:
                try:
                    self._global_batch_subscribers.remove(callback)
                except ValueError:
                    pass

            return unsub_global

        self._batch_subscribers.setdefault(channel_id, []).append(callback)

        def unsub() -> None:
            try:
                self._batch_subscribers[channel_id].remove(callback)
            except (ValueError, KeyError):
                pass

        return unsub

    def query(
        self,
        channel_id: str,
        *,
        session_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        last_n: int | None = None,
        max_points: int | None = None,
    ) -> pa.Table:
        """Query channel data, merging flushed files + in-memory buffer.

        Works mid-session (reads from buffer) and post-session (reads from files).

        Args:
            channel_id: Channel to query.
            session_id: If provided, only return data for this session
                (matched by first 8 chars of the session UUID).
            start: Filter rows after this time.
            end: Filter rows before this time.
            last_n: Return only the last N rows.
            max_points: Downsample to at most this many rows using LTTB
                (Largest Triangle Three Buckets). Preserves peaks and valleys
                for faithful visual representation. Applied after all other
                filters. Requires a ``value`` or ``samples`` column.
        """
        if self._index_enabled:
            return self._query_index(
                channel_id,
                session_id=session_id,
                start=start,
                end=end,
                last_n=last_n,
                max_points=max_points,
            )

        session_short = session_id[:8] if session_id else None
        writer = self._writers.get(channel_id)

        # Determine schema: from active writer, or try reading from disk
        schema: pa.Schema | None = writer.schema if writer else None

        tables: list[pa.Table] = []

        # Closed segment files from the active writer (already readable)
        # Skip if filtering to a different session
        active_paths: set[Path] = set()
        writer_session = str(self._session_id)[:8] if writer else None
        include_writer = writer and (session_short is None or session_short == writer_session)
        if include_writer and writer:
            for seg_path in writer.all_paths:
                active_paths.add(seg_path)
                try:
                    seg_reader = ipc.open_stream(pa.OSFile(str(seg_path), "rb"))
                    tables.append(seg_reader.read_all())
                except (pa.ArrowInvalid, OSError):
                    pass
            # Unflushed buffer
            if writer.buffer:
                buf_table = pa.table(
                    {col: [r[col] for r in writer.buffer] for col in writer.schema.names},
                    schema=writer.schema,
                )
                tables.append(buf_table)

        # Read from closed files on disk (other sessions or filtered session)
        if session_short:
            glob_pattern = f"*/{channel_id}_{session_short}*.arrow"
        else:
            glob_pattern = f"*/{channel_id}_*.arrow"
        for arrow_file in sorted(self._channels_dir.glob(glob_pattern)):
            if arrow_file in active_paths:
                continue
            try:
                reader = ipc.open_stream(pa.OSFile(str(arrow_file), "rb"))
                file_table = reader.read_all()
                tables.append(file_table)
                if schema is None:
                    schema = file_table.schema
            except (pa.ArrowInvalid, OSError):
                pass

        if not tables:
            # Fallback schema for empty results
            empty_schema = schema if schema is not None else SCALAR_SCHEMA
            return pa.table(
                {col: [] for col in empty_schema.names},
                schema=empty_schema,
            )

        result = pa.concat_tables(tables, promote_options="permissive")

        # Filter by time range. ``start``/``end`` filter against
        # ``received_at`` (the system-side write time, always present);
        # ``sampled_at`` is nullable and not all rows have it.
        if start is not None or end is not None:
            received = result.column("received_at").to_pylist()
            start_utc = _to_utc(start)
            end_utc = _to_utc(end)
            keep = [
                (not start_utc or ts >= start_utc) and (not end_utc or ts <= end_utc)
                for ts in received
            ]
            result = result.filter(keep)

        # Limit to last N
        if last_n is not None and len(result) > last_n:
            result = result.slice(len(result) - last_n)

        # LTTB decimation
        if max_points is not None and len(result) > max_points:
            result = _decimate_table(result, max_points)

        return result

    # ---- Warm DuckDB index (daemon-only; Opt 1) ----

    _INDEX_ENVELOPE = frozenset(
        {"received_at", "sampled_at", "source_method", "session_id", "sample_interval"}
    )
    _INDEX_ARROW_SCHEMA = pa.schema(
        [
            ("channel_id", pa.utf8()),
            ("session_id", pa.utf8()),
            ("received_at", pa.timestamp("us", tz="UTC")),
            ("sampled_at", pa.timestamp("us", tz="UTC")),
            ("source_method", pa.utf8()),
            ("sample_interval", pa.float64()),
            ("value", pa.utf8()),
        ]
    )

    def _index_open(self) -> None:
        """Open the on-disk index and fold in segments closed since last run.

        The index is a persistent derived cache (``_index.duckdb`` in the
        channels dir): it survives a daemon restart and is brought current
        by an **incremental** scan — only segments not already in the
        ``_ingested`` ledger are read (vs. the old wipe-and-rebuild-from-all
        on every start). Producer IPC files remain the durable truth.

        Live ``do_put`` rows ride a separate attached ``:memory:`` overlay
        (``live.channel_live``): they are ephemeral (lost on restart, then
        re-derived from their now-closed segments by the incremental scan),
        so they never collide with a segment-scanned row. Mirrors the runs
        daemon's persistent-index + in-memory-overlay split.
        """
        index_path = self._channels_dir / "_index.duckdb"
        self._index_db = duckdb.connect(str(index_path))
        self._ensure_index_schema(self._index_db)
        # Ephemeral live overlay: attached :memory: so it's visible to every
        # child read cursor (a register()'d temp view would not be), yet not
        # persisted — it's a projection of in-flight samples, re-derived from
        # segments on restart.
        self._index_db.execute("ATTACH ':memory:' AS live")
        self._index_db.execute(
            "CREATE TABLE live.channel_live AS SELECT * FROM channel_index LIMIT 0"
        )
        self._scan_disk()

    @staticmethod
    def _ensure_index_schema(conn: duckdb.DuckDBPyConnection) -> None:
        """Idempotently align the on-disk index schema (additive open).

        ``CREATE TABLE IF NOT EXISTS`` so a new column auto-migrates an
        existing DB on next spawn — no version bump, no re-ingest.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_index (
                channel_id VARCHAR,
                session_id VARCHAR,
                received_at TIMESTAMPTZ,
                sampled_at TIMESTAMPTZ,
                source_method VARCHAR,
                sample_interval DOUBLE,
                value VARCHAR
            )
            """
        )
        # Ledger of ingested segments — keyed on path alone. A channel
        # segment is written exactly once (one batch, then closed
        # immutable), so a path that's already recorded never needs
        # re-reading. (Events key on (path, mtime, size) because its IPC
        # files grow; channel segments don't.)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _ingested (path VARCHAR PRIMARY KEY, row_count BIGINT)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_channel_index_cid_recv "
            "ON channel_index(channel_id, received_at)"
        )

    def _index_cursor(self) -> duckdb.DuckDBPyConnection:
        """Thread-local read cursor over the shared in-memory index."""
        cur = getattr(self._index_local, "cur", None)
        if cur is None:
            assert self._index_db is not None
            cur = self._index_db.cursor()
            self._index_local.cur = cur
        return cur

    def _scan_disk(self) -> None:
        """Fold segments closed since last run into the persistent index.

        Incremental: a segment already in the ``_ingested`` ledger is
        skipped, so a daemon restart only reads new files rather than
        rebuilding the whole index from every segment.
        """
        if self._index_db is None:
            return
        pattern = re.compile(r"^(.+)_([0-9a-f]{8})(?:_\d+)?$")
        with self._index_lock:
            ingested = {
                row[0] for row in self._index_db.execute("SELECT path FROM _ingested").fetchall()
            }
        for arrow_file in sorted(self._channels_dir.glob("*/*.arrow")):
            path_str = str(arrow_file)
            if path_str in ingested:
                continue
            m = pattern.match(arrow_file.stem)
            if not m:
                continue
            try:
                reader = ipc.open_stream(pa.OSFile(str(arrow_file), "rb"))
                table = reader.read_all()
            except (pa.ArrowInvalid, OSError):
                # Torn / still-open segment — leave it out of the ledger so
                # the next restart re-reads it once it's a complete file.
                continue
            self._absorb_descriptor(m.group(1), table.schema)
            rows = self._segment_rows_to_index(m.group(1), table)
            self._insert_index_rows(rows, "channel_index", ledger_path=path_str)

    def _absorb_descriptor(self, channel_id: str, schema: pa.Schema) -> None:
        """Populate the served descriptor map from a segment's schema metadata.

        Producers stamp the ``ChannelDescriptor`` as Arrow schema metadata on
        every segment; the daemon (which never calls ``write()``) reads it here
        so ``list_channel_info`` can serve it.
        """
        if channel_id in self._registry:
            return
        meta = (schema.metadata or {}).get(b"litmus.channel_descriptor")
        if meta:
            self._registry[channel_id] = ChannelDescriptor.model_validate_json(meta)

    @classmethod
    def _segment_rows_to_index(cls, channel_id: str, table: pa.Table) -> list[dict[str, Any]]:
        """Convert a typed segment table to index rows (``value`` JSON-encoded).

        ``channel_id`` comes from the filename (segments don't store it);
        ``session_id`` from the row column. Scalar/array rows carry a
        ``value`` column; dict/struct rows fold their non-envelope columns
        back into one JSON object.
        """
        out: list[dict[str, Any]] = []
        for r in table.to_pylist():
            if "value" in r:
                payload = r["value"]
            else:
                payload = {k: v for k, v in r.items() if k not in cls._INDEX_ENVELOPE}
            out.append(
                {
                    "channel_id": channel_id,
                    "session_id": r.get("session_id"),
                    "received_at": r.get("received_at"),
                    "sampled_at": r.get("sampled_at"),
                    "source_method": r.get("source_method") or "",
                    "sample_interval": r.get("sample_interval"),
                    "value": encode_value(payload),
                }
            )
        return out

    def _insert_index_rows(
        self,
        rows: list[dict[str, Any]],
        table: str,
        *,
        ledger_path: str | None = None,
    ) -> None:
        """Insert index rows under the write lock (single writer).

        ``table`` is ``channel_index`` (durable, segment-scanned) or
        ``live.channel_live`` (ephemeral overlay). ``ledger_path``, when
        given, records the source segment in ``_ingested`` in the SAME
        transaction as the insert, so a crash can't half-record a segment.
        """
        if self._index_db is None:
            return
        if not rows:
            # An empty segment still needs its ledger mark so it isn't
            # re-read on every restart.
            if ledger_path is not None:
                with self._index_lock:
                    self._index_db.execute(
                        "INSERT OR IGNORE INTO _ingested (path, row_count) VALUES (?, 0)",
                        [ledger_path],
                    )
            return
        tbl = pa.Table.from_pylist(rows, schema=self._INDEX_ARROW_SCHEMA)
        with self._index_lock:
            self._index_db.register("_incoming", tbl)
            self._index_db.execute(f"INSERT INTO {table} SELECT * FROM _incoming")
            self._index_db.unregister("_incoming")
            if ledger_path is not None:
                self._index_db.execute(
                    "INSERT OR IGNORE INTO _ingested (path, row_count) VALUES (?, ?)",
                    [ledger_path, len(rows)],
                )

    @staticmethod
    def _payload_and_interval(sample: ChannelSample) -> tuple[Any, float | None]:
        """Split an array sample's ``{value, sample_interval}`` envelope.

        ``_normalize_value`` folds arrays into ``{"value": [...],
        "sample_interval": dt}``, which is what rides on the live sample.
        Segments store the array in the ``value`` column with
        ``sample_interval`` alongside, so the index must too — otherwise a
        live-ingested array would encode differently from a disk-scanned one.
        """
        v = sample.value
        if (
            isinstance(v, dict)
            and set(v.keys()) == {"value", "sample_interval"}
            and isinstance(v.get("value"), list)
        ):
            return v["value"], v["sample_interval"]
        return v, sample.sample_interval

    def _index_row(self, channel_id: str, sample: ChannelSample) -> dict[str, Any]:
        """Build one index row from a sample (``value`` JSON-encoded)."""
        payload, interval = self._payload_and_interval(sample)
        return {
            "channel_id": channel_id,
            "session_id": sample.session_id,
            "received_at": sample.received_at,
            "sampled_at": sample.sampled_at,
            "source_method": sample.source_method or "",
            "sample_interval": interval,
            "value": encode_value(payload),
        }

    def _pending_extend(self, rows: list[dict[str, Any]]) -> None:
        """Append index rows to the pending buffer; flush past the threshold."""
        if not rows:
            return
        with self._pending_lock:
            self._pending.extend(rows)
            overflowed = len(self._pending) >= self._pending_threshold
        if overflowed:
            self._flush_pending()

    def ingest_batch(self, channel_id: str, batch: pa.RecordBatch) -> None:
        """Daemon do_put path: index live samples + fan out (no segment persist).

        Replaces ``store.write`` on the daemon side (Opt 1: the daemon
        does not persist a second segment copy). Live rows reach the index
        only via the pending buffer flush — never via the disk scan — so
        there is no overlap to dedup.
        """
        # Fan out the whole batch ONCE (cross-process relay) before the per-row
        # index work — no re-explosion to per-sample deliveries.
        self._notify_batch(channel_id, batch)
        rows: list[dict[str, Any]] = []
        for i in range(batch.num_rows):
            sample = batch_row_to_sample(batch, i)
            rows.append(self._index_row(channel_id, sample))
            self._notify(channel_id, sample)
        self._pending_extend(rows)

    def _flush_pending(self) -> None:
        """Move pending live rows into the index."""
        with self._pending_lock:
            if not self._pending:
                return
            pending = self._pending
            self._pending = []
        # Live rows land in the ephemeral overlay, NOT the durable index:
        # their durable copy is the producer segment, folded into
        # channel_index by the incremental scan on the next restart.
        self._insert_index_rows(pending, "live.channel_live")

    def _query_index(
        self,
        channel_id: str,
        *,
        session_id: str | None,
        start: datetime | None,
        end: datetime | None,
        last_n: int | None,
        max_points: int | None,
    ) -> pa.Table:
        """At-rest query served from the warm index (∪ pending buffer)."""
        self._flush_pending()
        cur = self._index_cursor()
        # Union the durable index with the live overlay (same columns); a
        # sample is in exactly one of them — overlay until its segment is
        # scanned on the next restart, channel_index after.
        sql = [
            "SELECT received_at, sampled_at, value, source_method, "
            "session_id, sample_interval FROM ("
            "SELECT * FROM channel_index UNION ALL SELECT * FROM live.channel_live"
            ") WHERE channel_id = ?"
        ]
        params: list[Any] = [channel_id]
        if session_id:
            sql.append("AND left(session_id, 8) = left(?, 8)")
            params.append(session_id)
        start_utc = _to_utc(start)
        end_utc = _to_utc(end)
        if start_utc is not None:
            sql.append("AND received_at >= ?")
            params.append(start_utc)
        if end_utc is not None:
            sql.append("AND received_at <= ?")
            params.append(end_utc)
        sql.append("ORDER BY received_at")
        table = cur.execute(" ".join(sql), params).arrow().read_all()

        if last_n is not None and table.num_rows > last_n:
            table = table.slice(table.num_rows - last_n)
        table = self._decode_value_column(table)
        if max_points is not None and table.num_rows > max_points:
            table = _decimate_table(table, max_points)
        return table

    @staticmethod
    def _decode_value_column(table: pa.Table) -> pa.Table:
        """JSON-decode the VARCHAR ``value`` column back to typed values.

        Inverse of ``encode_value``: non-JSON strings pass through (matches
        ``batch_row_to_sample``). Values within one channel are homogeneous,
        so Arrow infers a single column type.
        """
        if "value" not in table.column_names or table.num_rows == 0:
            return table
        decoded: list[Any] = []
        for v in table.column("value").to_pylist():
            if v is None:
                decoded.append(None)
                continue
            try:
                decoded.append(json.loads(v))
            except (json.JSONDecodeError, TypeError):
                decoded.append(v)
        idx = table.column_names.index("value")
        return table.set_column(idx, "value", pa.array(decoded))

    def _connect_or_serve(self) -> None:
        """Acquire a ref-counted Flight server daemon.

        First caller spawns a detached daemon process running the Flight
        server.  Subsequent callers increment the ref count and connect.
        On ``close()``, the ref is released.  The daemon exits after an
        idle timeout once all refs are gone.
        """
        location = flight_manager.acquire(
            self._channels_dir,
            self._flight_host,
            self._flight_port,
        )
        self._flight_location = location

    def _flight_release(self) -> None:
        """Release our ref on the Flight server daemon."""
        flight_manager.release(self._channels_dir)

    def _flight_push(self, channel_id: str, sample: ChannelSample) -> None:
        """Push a sample to the Flight daemon via do_put.

        Non-fatal: data is in IPC files, daemon rebuilds on restart.
        """
        location = self._flight_location
        if location is None:
            return
        try:
            client = self._flight_client
            if client is None:
                client = flight.connect(location)
                self._flight_client = client
            batch = sample_to_batch(sample)
            writer = self._flight_writers.get(channel_id)
            if writer is None:
                descriptor = flight.FlightDescriptor.for_command(
                    channel_id.encode("utf-8"),
                )
                # Stamp the descriptor on the persistent stream schema so the
                # daemon absorbs it on stream-open — live channels are served
                # their full descriptor before any segment closes (same
                # Arrow-native carrier as the segments).
                put_schema = batch.schema
                desc_obj = self._registry.get(channel_id)
                if desc_obj is not None:
                    put_schema = put_schema.with_metadata(
                        {b"litmus.channel_descriptor": desc_obj.model_dump_json().encode()}
                    )
                writer, _ = client.do_put(descriptor, put_schema)
                self._flight_writers[channel_id] = writer
            # write_batch on the held stream flushes per batch (gRPC client
            # streaming sends each message), so cross-process subscribers see
            # the sample immediately — the stream stays open, not closed.
            writer.write_batch(batch)
        except (OSError, RuntimeError, pa.ArrowException) as exc:
            warnings.warn(f"Channel Flight push failed (non-fatal): {exc}", stacklevel=2)
            self._reset_flight()

    def _push_loop(self) -> None:
        """Background consumer of the push queue (async-push mode). Drains
        samples and does the Flight do_put OFF the write path, so a slow daemon
        or subscriber never backpressures capture."""
        q = self._push_queue
        stop = self._push_stop
        if q is None or stop is None:
            return
        while not stop.is_set() or not q.empty():
            try:
                channel_id, sample = q.get(timeout=0.1)
            except queue.Empty:
                continue
            self._flight_push(channel_id, sample)

    def _reset_flight(self) -> None:
        """Tear down the Flight client + all held writers after an error, so
        the next push reconnects and reopens streams (the broken stream can't
        be reused). Data is durable in IPC files; the daemon rebuilds on
        restart, so a dropped push is non-fatal."""
        for writer in self._flight_writers.values():
            try:
                writer.close()
            except (OSError, RuntimeError, pa.ArrowException):
                pass
        self._flight_writers.clear()
        self._flight_client = None

    @property
    def flight_location(self) -> str | None:
        """The gRPC location of the Flight server, if running."""
        return self._flight_location

    @property
    def push_drops(self) -> int:
        """Live samples dropped when the async push queue overflowed.

        Live = from-now; an overflow drop never affects the durable segment
        (which is whole). Non-zero means a subscriber/daemon couldn't keep up
        with capture — the live feed is lossy under that load, by design.
        """
        return self._push_drops

    @property
    def session_id(self) -> UUID:
        """The session this store writes channels under."""
        return self._session_id

    @staticmethod
    def list_channel_refs(date_dirs: list[Path]) -> set[tuple[str, str]]:
        """List (channel_id, session_short) pairs stored in the given date dirs.

        Used by retention/materialize to discover which channel data lives
        in directories that are about to be pruned.

        Args:
            date_dirs: Channel date directories to scan.

        Returns:
            Set of (channel_id, session_short) pairs.
        """
        # session_short is always 8 hex chars (first 8 of UUID)
        # Filename: {channel_id}_{session_short}.arrow or {channel_id}_{session_short}_NNN.arrow
        pattern = re.compile(r"^(.+)_([0-9a-f]{8})(?:_\d+)?$")

        refs: set[tuple[str, str]] = set()
        for dir_path in date_dirs:
            if not dir_path.is_dir():
                continue
            for arrow_file in dir_path.rglob("*.arrow"):
                m = pattern.match(arrow_file.stem)
                if m:
                    refs.add((m.group(1), m.group(2)))
        return refs

    def close(self) -> None:
        """Flush all writers, write channel registry, close.

        Position 2 (item 4b): emits one :class:`ChannelClosed`
        (``reason="session_ended"``) per channel that received at least
        one write in this session, paired with the ``ChannelStarted``
        that fired on first write. Idempotent — second close() emits
        nothing.
        """
        # Position 2: emit ChannelClosed for every channel touched in
        # this session — before tearing down Flight / writers so the
        # event log captures the lifecycle marker while the event log
        # is still live.
        if not self._closed and self._event_log is not None:
            for channel_id in list(self._registry):
                self._event_log.emit(
                    ChannelClosed(
                        session_id=self._session_id,
                        run_id=self._channel_run_ids.get(channel_id),
                        channel_id=channel_id,
                        reason="session_ended",
                    )
                )
        self._closed = True

        # Stop the async pusher (if any) and let it drain before tearing down
        # the writers it uses.
        if self._push_thread is not None:
            if self._push_stop is not None:
                self._push_stop.set()
            self._push_thread.join(timeout=5.0)
            self._push_thread = None

        # Close held do_put writers (flushes any in-flight batch) before the
        # client and server ref.
        for writer in self._flight_writers.values():
            try:
                writer.close()
            except (OSError, RuntimeError, pa.ArrowException):
                pass
        self._flight_writers.clear()

        # Close Flight client and release server ref
        if self._flight_client is not None:
            try:
                self._flight_client.close()
            except (OSError, RuntimeError):
                pass
            self._flight_client = None
        if self._flight_location is not None:
            self._flight_release()
            self._flight_location = None

        try:
            for writer in self._writers.values():
                try:
                    writer.close()
                except _WRITE_ERRORS as exc:
                    warnings.warn(
                        f"ChannelStore failed to write '{writer.channel_id}': {exc}",
                        stacklevel=2,
                    )

            if self._index_db is not None:
                try:
                    self._index_db.close()
                except (OSError, duckdb.Error):
                    pass
                self._index_db = None
        finally:
            self._writers.clear()
            self._subscribers.clear()
            self._global_subscribers.clear()
