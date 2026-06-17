"""Channel store — streaming Arrow IPC with live subscriptions.

Producers write directly via write(). Stores data in per-channel Arrow IPC
files with flexible per-channel schemas inferred from the first write.
Supports live in-process subscriptions via on_channel().
"""

from __future__ import annotations

import itertools
import os
import re
import socket
import warnings
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litmus.data.event_log import EventLog
from uuid import UUID

import pyarrow as pa
import pyarrow.flight as flight
import pyarrow.ipc as ipc

from litmus.data._ipc_writer import BufferedIPCWriter
from litmus.data._push_relay import PushRelay
from litmus.data.channels import flight_manager
from litmus.data.channels.index import ChannelIndex, _decimate_table, _to_utc
from litmus.data.channels.models import (
    CHANNELS_PUT_COMMAND,
    SCALAR_SCHEMA,
    ChannelDescriptor,
    ChannelSample,
    _data_type_for,
    _infer_schema,
    batch_row_to_sample,
    encode_value,
    sample_schema,
    samples_to_batch,
)
from litmus.data.events import ChannelEnded, ChannelStarted, StreamCheckpoint
from litmus.data.ref import classify_value, make_channel_uri
from litmus.models.data_options import ChannelOptions

_WRITE_ERRORS = (OSError, pa.ArrowException)  # type: ignore[attr-defined]


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
        flush_interval: float = 1.0,
    ) -> None:
        super().__init__(
            path=path,
            schema=schema,
            flush_threshold=flush_threshold,
            flush_interval=flush_interval,
        )
        self.channel_id = channel_id
        self.data_type = data_type
        # Template: /dir/channel_id_session.arrow → segments append _NNN
        self._path_template = path
        self._segment = 0
        self._closed_paths: list[Path] = []
        # Channels buffer whole RecordBatches (the columnar write core builds
        # them) and flush+rotate once buffered rows reach the threshold — so
        # per-sample writes accumulate into one segment instead of one file each,
        # while staying queryable in memory before the flush (the analogue of the
        # base's per-row dict buffer, which channels no longer use).
        self._pending_batches: list[pa.RecordBatch] = []
        self._pending_rows = 0

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

    def append_batch(self, batch: pa.RecordBatch) -> pa.RecordBatch:
        """Buffer a columnar batch; flush + rotate once buffered rows reach the
        threshold (or the idle timer fires). Overrides the base's immediate write
        so small blocks coalesce into segment-sized files instead of one each."""
        with self._lock:
            self._pending_batches.append(batch)
            self._pending_rows += batch.num_rows
            if self._pending_rows >= self._flush_threshold:
                self._flush_pending()
            elif self._timer is None:
                self._start_timer()
        return batch

    def _flush_pending(self) -> None:
        """Write buffered batches as one segment, then rotate. Caller holds lock."""
        if not self._pending_batches:
            return
        self._cancel_timer()
        writer = self._ensure_writer()
        for b in self._pending_batches:
            writer.write_batch(b)
        self._row_count += self._pending_rows
        flushed = self._pending_batches[-1]
        self._pending_batches = []
        self._pending_rows = 0
        self._on_flush(flushed)

    def _timer_flush(self) -> None:
        """Idle-timer flush — drains the pending batches (not the dict buffer)."""
        with self._lock:
            self._timer = None
            self._flush_pending()

    def pending_table(self) -> pa.Table | None:
        """Buffered-but-unflushed rows as one table — for mid-session query."""
        with self._lock:
            if not self._pending_batches:
                return None
            return pa.Table.from_batches(self._pending_batches)

    def close(self) -> int:
        """Flush pending batches, then close the open segment."""
        with self._lock:
            self._cancel_timer()
            self._flush_pending()
        if self._writer is not None:
            try:
                self._writer.close()
            except _WRITE_ERRORS as exc:
                warnings.warn(f"Failed to close IPC writer: {exc}", stacklevel=2)
            self._writer = None
        return self._row_count

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
        flush_threshold: int | None = None,
        *,
        options: ChannelOptions | None = None,
        serve: bool = False,
        host: str = "127.0.0.1",
        port: int = 0,
        event_log: EventLog | None = None,
        index: bool = False,
        station_hostname: str | None = None,
        checkpoint_cadence: float | None = None,
    ) -> None:
        # Parent-only convention — caller passes the results parent
        # (containing ``runs/``, ``channels/``, ``events/`` …); the
        # store owns its ``channels/`` subdir. Mirrors RunStore /
        # StepsQuery / MeasurementsQuery / EventStore.
        self._channels_dir = data_dir / "channels"
        self._session_id = session_id
        # The store runs in the producer process, so its own host IS the channel's
        # host — resolve it here rather than depending on a station config (which
        # not every producer path has). Tests may pass an explicit value.
        self._station_hostname = station_hostname or socket.gethostname()
        # Producer-local data options (litmus.yaml ``channels:``). An explicit
        # ``flush_threshold`` overrides ``options.writer_flush_threshold`` — the
        # one knob with a direct shortcut, since it's the dominant test lever.
        self._options = options or ChannelOptions()
        self._flush_threshold = (
            flush_threshold if flush_threshold is not None else self._options.writer_flush_threshold
        )
        self._writers: dict[str, _ChannelWriter] = {}
        self._registry: dict[str, ChannelDescriptor] = {}
        # Per-(channel, session) monotonic write position. This store is
        # session-scoped, so one counter per channel mirrors EventStore's
        # per-writer event_offset; next() on itertools.count is atomic under
        # the GIL, so concurrent writes to one channel get distinct values.
        self._channel_seq: dict[str, itertools.count] = {}
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
        # ChannelEnded emission because per-(channel, session)
        # tracking lives here naturally — the registry already records
        # first write. Any writer path (observer.read /
        # Context.stream / channels.write / FileStore stream sink)
        # gets the right lifecycle events without coordinating its
        # own tracker. ``event_log`` may be ``None`` for tests /
        # bringup paths with no session event log.
        self._event_log = event_log
        # Stream liveness checkpoint: when set, the write path emits one
        # ``StreamCheckpoint`` (carrying offset) per ``checkpoint_cadence`` of
        # off-spine writing, so a long active channel stream renews the session
        # lease instead of going silent on the spine. ``_last_spine_emit`` tracks
        # the store's most recent event-log emission (ChannelStarted / checkpoint
        # / ChannelEnded) — any of them resets the cadence clock.
        self._checkpoint_cadence = checkpoint_cadence
        self._last_spine_emit: datetime | None = None
        # First-write run_id per channel — pairs with ChannelEnded
        # on session-end so the two events carry the same run context.
        # ``None`` for channels written outside any run (daemon writes,
        # interactive bringup).
        self._channel_run_ids: dict[str, UUID | None] = {}
        self._closed = False
        # Lazy daemon spin: the store object is cheap, but ``open()`` (Flight
        # serve + push thread) is the ~per-session cost. A session that writes
        # zero channels must spin no daemon, so ``open()`` is deferred to the
        # first append (see ``_append_and_publish``) and is idempotent.
        self._opened = False

        # Warm DuckDB index — the session-less reader (``ChannelIndex``) the
        # store composes when indexing is enabled: the daemon indexes producer
        # files and serves at-rest query (Opt 1 — no second segment copy), and
        # warm-index tests write + index through one object. The index is a
        # derived cache (producer IPC files are the durable truth); ``None`` on a
        # pure producer (``serve=True`` writers persist + push, no local index).
        self._index = ChannelIndex(self._channels_dir) if index else None

        # The synchronous daemon push is OFF the write path: write() enqueues the
        # sample and a background thread does the Flight do_put, so capture runs
        # at durable-append speed regardless of daemon/subscriber drain. The live
        # feed drops on overflow (live = from-now); the durable segment is whole.
        # Default on; LITMUS_CHANNELS_SYNC_PUSH=1 forces the inline sync push
        # (A/B benchmarking + rollback).
        self._async_push = os.environ.get("LITMUS_CHANNELS_SYNC_PUSH") != "1"
        self._push_relay: PushRelay | None = None

    def open(self) -> None:
        if self._opened:
            return
        self._channels_dir.mkdir(parents=True, exist_ok=True)
        if self._index is not None:
            self._index.open()
        if self._serve:
            self._connect_or_serve()
            if self._async_push and self._flight_location is not None:
                self._push_relay = PushRelay(
                    flush=self._push_flush,
                    key=lambda item: item[0],
                    weight=lambda item: item[1].num_rows,
                    max_weight=self._options.push_max_rows,
                    max_wait=self._options.push_max_wait,
                    queue_max=self._options.push_queue_max,
                    thread_name="channel-pusher",
                )
        # Set last: a failed open() can be retried; a single producer thread means
        # no concurrent re-entry to guard against (see the single-writer model).
        self._opened = True

    def list_channel_info(self) -> list[tuple[ChannelDescriptor, pa.Schema]]:
        """Return (descriptor, schema) for each known channel.

        A writer populates ``_registry`` on first write (producer / warm-index
        test); the daemon absorbs descriptors into its index (no writer, so the
        schema falls back to ``SCALAR_SCHEMA``). Both are surfaced.
        """
        out: dict[str, tuple[ChannelDescriptor, pa.Schema]] = {}
        for cid, desc in self._registry.items():
            writer = self._writers.get(cid)
            out[cid] = (desc, writer.schema if writer else SCALAR_SCHEMA)
        if self._index is not None:
            for desc in self._index.descriptors():
                out.setdefault(desc.channel_id, (desc, SCALAR_SCHEMA))
        return list(out.values())

    def get_channel_schema(self, channel_id: str) -> pa.Schema | None:
        """Return the Arrow schema for a channel, or None if unknown."""
        writer = self._writers.get(channel_id)
        if writer is not None:
            return writer.schema
        if channel_id in self._registry:
            return SCALAR_SCHEMA
        if self._index is not None and self._index.has(channel_id):
            return SCALAR_SCHEMA
        return None

    def _ensure_writer(
        self,
        channel_id: str,
        first_value: object,
        sample_interval: float | None,
        data_type: str,
    ) -> _ChannelWriter:
        """Return the channel's segment writer, creating it (schema inferred from
        ``first_value``) on first use. ``_register`` must have run first — the
        segment schema carries the registry descriptor as Arrow metadata."""
        writer = self._writers.get(channel_id)
        if writer is not None:
            return writer
        schema = _infer_schema(self._normalize_value(first_value, sample_interval)).with_metadata(
            {b"litmus.channel_descriptor": self._registry[channel_id].model_dump_json().encode()}
        )
        session_short = str(self._session_id)[:8]
        today = date.today().isoformat()
        path = self._channels_dir / today / f"{channel_id}_{session_short}.arrow"
        writer = _ChannelWriter(
            channel_id,
            data_type,
            schema,
            path,
            self._flush_threshold,
            self._options.writer_flush_interval,
        )
        self._writers[channel_id] = writer
        return writer

    def _register(  # noqa: PLR0913
        self,
        channel_id: str,
        *,
        data_type: str | None = None,
        units: str | None = None,
        instrument_role: str = "",
        resource: str = "",
        attributes: dict[str, Any] | None = None,
        source: str = "",
        run_id: UUID | None = None,
        now: datetime | None = None,
    ) -> None:
        """Register a channel's identity once, or validate a re-declare / write
        against the established identity.

        Identity is immutable within a session: a conflicting ``units`` (or
        ``data_type``, once a writer has locked it) raises instead of being
        silently ignored. Before the first write (declare-only), the type/units
        are still open — the first write fills them in.
        """
        existing = self._registry.get(channel_id)
        if existing is not None:
            if units is not None and existing.units is not None and units != existing.units:
                raise ValueError(
                    f"Channel '{channel_id}': unit {existing.units!r} is fixed for this "
                    f"session; cannot change to {units!r}"
                )
            if (
                data_type is not None
                and channel_id in self._writers
                and existing.data_type != data_type
            ):
                raise ValueError(
                    f"Channel '{channel_id}': type {existing.data_type!r} is fixed for "
                    f"this session; cannot change to {data_type!r}"
                )
            # Declare-only so far (no writer): let the first write fill in type/units.
            if channel_id not in self._writers:
                if data_type is not None:
                    existing.data_type = data_type
                if units is not None and existing.units is None:
                    existing.units = units
            return

        self._registry[channel_id] = ChannelDescriptor(
            channel_id=channel_id,
            data_type=data_type or "scalar:float",
            units=units,
            instrument_role=instrument_role,
            resource=resource,
            attributes=dict(attributes) if attributes else {},
            first_seen=now or datetime.now(UTC),
            hostname=self._station_hostname,
            session_id=str(self._session_id),
        )
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
            self._last_spine_emit = now or datetime.now(UTC)

    def _maybe_checkpoint(self, channel_id: str, offset: int, run_id: UUID | None) -> None:
        """Emit a ``StreamCheckpoint`` if a cadence of off-spine writing has
        elapsed since the store's last spine event — bounded to one per cadence,
        carrying the channel's offset so the session lease renews and progress
        is recorded. No-op without a cadence or event log."""
        if self._checkpoint_cadence is None or self._event_log is None:
            return
        now = datetime.now(UTC)
        if (
            self._last_spine_emit is not None
            and (now - self._last_spine_emit).total_seconds() < self._checkpoint_cadence
        ):
            return
        self._event_log.emit(
            StreamCheckpoint(
                session_id=self._session_id,
                run_id=run_id,
                uri=make_channel_uri(channel_id, str(self._session_id), sample_offset=offset),
                offset=offset,
            )
        )
        self._last_spine_emit = now

    def declare(
        self,
        channel_id: str,
        *,
        units: str | None = None,
        instrument_role: str = "",
        resource: str = "",
        attributes: dict[str, Any] | None = None,
        run_id: UUID | None = None,
    ) -> None:
        """Declare a channel's identity for this session (the producer's
        establishing verb).

        Sets ``units``/``instrument_role``/``resource``/``attributes`` once; the
        value type is locked by the first write. Idempotent for matching args;
        a conflicting unit raises. Optional — a first write auto-registers with
        defaults — but it's the only way to attach units up front.
        """
        if "/" in channel_id or "\\" in channel_id or ".." in channel_id:
            raise ValueError(
                f"Invalid channel_id '{channel_id}': must not contain path separators or '..'"
            )
        self._register(
            channel_id,
            units=units,
            instrument_role=instrument_role,
            resource=resource,
            attributes=attributes,
            run_id=run_id,
        )

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
        # One sample is the N=1 case of the shared core (see _append_and_publish).
        return self._append_and_publish(
            channel_id,
            values=[value],
            sampled_ats=[sampled_at],
            source=source,
            units=units,
            sample_interval=sample_interval,
            instrument_role=instrument_role,
            resource=resource,
            attributes=attributes,
            run_id=run_id,
        )

    def write_many(  # noqa: PLR0913
        self,
        channel_id: str,
        samples: Sequence[Any],
        *,
        units: str | None = None,
        sample_interval: float | None = None,
        source: str = "observe",
        instrument_role: str = "",
        resource: str = "",
        attributes: dict[str, Any] | None = None,
        run_id: UUID | None = None,
    ) -> str:
        """Write a batch of samples in one call — ``write``, batched.

        ``samples`` is either a plain list of values ``[v, ...]`` or a list of
        ``(value, sampled_at)`` pairs ``[(v, ts), ...]`` when you have per-sample
        timestamps. ``value`` is anything ``write`` accepts (scalar, array,
        dict); ``sampled_at`` is its hardware instant, or ``None``. N elements
        become N individually addressable rows — the opposite of
        ``write(channel, [a, b, c])``, which stores one waveform (one row). The
        whole batch is one durable append and one gRPC message instead of N. The
        keyword arguments are batch-level metadata and apply to every sample,
        exactly as on ``write``.
        """
        if not samples:
            return make_channel_uri(channel_id, str(self._session_id))

        # Accept bare values ``[v, ...]`` OR ``(value, sampled_at)`` pairs. Decide
        # once from the first element: a pair is a 2-tuple whose second item is a
        # timestamp (datetime) or None. Split into parallel value/timestamp columns
        # and hand the block to the shared core — N samples is just the N>1 case.
        first_el = samples[0]
        paired = (
            isinstance(first_el, tuple)
            and len(first_el) == 2
            and (first_el[1] is None or isinstance(first_el[1], datetime))
        )
        if paired:
            values = [e[0] for e in samples]
            sampled_ats: list[datetime | None] = [e[1] for e in samples]
        else:
            values = list(samples)
            sampled_ats = [None] * len(samples)

        return self._append_and_publish(
            channel_id,
            values=values,
            sampled_ats=sampled_ats,
            source=source,
            units=units,
            sample_interval=sample_interval,
            instrument_role=instrument_role,
            resource=resource,
            attributes=attributes,
            run_id=run_id,
        )

    def _has_batch_subs(self, channel_id: str) -> bool:
        """Any co-located batch subscriber (a Flight server wrapping THIS store)."""
        return bool(self._global_batch_subscribers or self._batch_subscribers.get(channel_id))

    def _append_and_publish(  # noqa: PLR0913
        self,
        channel_id: str,
        *,
        values: Sequence[Any],
        sampled_ats: Sequence[datetime | None],
        source: str,
        units: str | None = None,
        sample_interval: float | None = None,
        instrument_role: str = "",
        resource: str = "",
        attributes: dict[str, Any] | None = None,
        run_id: UUID | None = None,
    ) -> str:
        """Append a block of 1+ samples to the durable segment, then best-effort
        publish — the ONE body behind ``write`` / ``write_many`` / the stream sink.

        The three verbs differ only in batching granularity (1 / N / streamed);
        this is the shared mechanism. The durable segment is written FIRST (every
        call, complete) — the live relay and the index only ever lag under push
        overflow, never lose (visibility trails the durable frontier, never
        durability).

        Scalar blocks take a columnar fast path: each column is one ``pa.array``,
        no per-sample ``ChannelSample`` or per-row dict. Array/struct/dict values
        take the per-row build (their envelope needs it). ``ChannelSample`` objects
        are materialized only when an in-process per-sample subscriber or the local
        index actually consumes them — never on the common capture path.
        """
        if not values:
            return make_channel_uri(channel_id, str(self._session_id))
        if "/" in channel_id or "\\" in channel_id or ".." in channel_id:
            raise ValueError(
                f"Invalid channel_id '{channel_id}': must not contain path separators or '..'"
            )
        first = values[0]
        if classify_value(first) == "blob":
            raise ValueError(
                f"Channel {channel_id}: value type {type(first).__name__} is not numeric. "
                "Use file:// refs for non-numeric data."
            )

        # Lazy daemon spin: the first real append opens the store (Flight serve +
        # push thread). Idempotent — a no-op for an already-open store.
        self.open()

        now = datetime.now(UTC)
        sid = str(self._session_id)
        n = len(values)
        # One contiguous offset block — next() is atomic, single producer thread.
        counter = self._channel_seq.setdefault(channel_id, itertools.count())
        offsets = list(itertools.islice(counter, n))
        sampled = [_to_utc(t) for t in sampled_ats]
        # Per-sample objects only when something downstream actually reads them.
        need_samples = bool(
            self._index is not None or self._subscribers.get(channel_id) or self._global_subscribers
        )

        if isinstance(first, (bool, int, float, str)):
            # ---- Scalar fast path: columnar, no per-row objects ----
            data_type = _data_type_for(first)
            self._register(
                channel_id,
                data_type=data_type,
                units=units,
                instrument_role=instrument_role,
                resource=resource,
                attributes=attributes,
                source=source,
                run_id=run_id,
                now=now,
            )
            writer = self._ensure_writer(channel_id, first, None, data_type)
            received = pa.array([now] * n, type=pa.timestamp("us", tz="UTC"))
            sampled_col = pa.array(sampled, type=pa.timestamp("us", tz="UTC"))
            # Durable segment FIRST — typed columns, one pa.array each.
            writer.append_batch(
                pa.record_batch(
                    {
                        "received_at": received,
                        "sampled_at": sampled_col,
                        "value": pa.array(values, type=writer.schema.field("value").type),
                        "source_method": pa.array([source] * n, type=pa.utf8()),
                        "session_id": pa.array([sid] * n, type=pa.utf8()),
                        "sample_offset": pa.array(offsets, type=pa.int64()),
                    },
                    schema=writer.schema,
                )
            )
            # Live wire (sample_schema, JSON value) — identical to samples_to_batch,
            # built only when there's somewhere to send it.
            wire = None
            if self._flight_location is not None or self._has_batch_subs(channel_id):
                wire = pa.record_batch(
                    {
                        "channel_id": pa.array([channel_id] * n, type=pa.utf8()),
                        "received_at": received,
                        "sampled_at": sampled_col,
                        "value": pa.array([encode_value(v) for v in values], type=pa.utf8()),
                        "source_method": pa.array([source] * n, type=pa.utf8()),
                        "units": pa.array([units or ""] * n, type=pa.utf8()),
                        "sample_interval": pa.array([sample_interval] * n, type=pa.float64()),
                        "session_id": pa.array([sid] * n, type=pa.utf8()),
                        "sample_offset": pa.array(offsets, type=pa.int64()),
                    },
                    schema=sample_schema(),
                )
            samples = (
                [
                    ChannelSample(
                        channel_id=channel_id,
                        received_at=now,
                        sampled_at=sampled[i],
                        value=values[i],
                        units=units,
                        sample_interval=sample_interval,
                        source_method=source,
                        session_id=sid,
                        sample_offset=offsets[i],
                    )
                    for i in range(n)
                ]
                if need_samples
                else None
            )
            self._publish(channel_id, wire, samples)
            self._maybe_checkpoint(channel_id, offsets[-1], run_id)
            return make_channel_uri(channel_id, sid, sample_offset=offsets[0] if n == 1 else None)

        # ---- Array / struct / dict: per-row build (the envelope needs it) ----
        rows: list[dict] = []
        samples = []
        data_type = "scalar:float"
        for i, value in enumerate(values):
            dt, row, sample = self._to_arrow_row(
                channel_id,
                value,
                now,
                source,
                units=units,
                sample_interval=sample_interval,
                sampled_at=sampled[i],
            )
            if row is None:
                raise ValueError(f"Channel {channel_id}: could not classify value")
            if i == 0:
                data_type = dt
            row["sample_offset"] = offsets[i]
            if sample is not None:
                sample.sample_offset = offsets[i]
                samples.append(sample)
            rows.append(row)
        self._register(
            channel_id,
            data_type=data_type,
            units=units,
            instrument_role=instrument_role,
            resource=resource,
            attributes=attributes,
            source=source,
            run_id=run_id,
            now=now,
        )
        writer = self._ensure_writer(channel_id, first, sample_interval, data_type)
        writer.append_batch(
            pa.record_batch(
                {col: [r[col] for r in rows] for col in writer.schema.names},
                schema=writer.schema,
            )
        )
        wire = (
            samples_to_batch(samples)
            if samples and (self._flight_location is not None or self._has_batch_subs(channel_id))
            else None
        )
        self._publish(channel_id, wire, samples if need_samples else None)
        self._maybe_checkpoint(channel_id, offsets[-1], run_id)
        # sample_offset pins single-sample writes (write/observe) to their one row;
        # the batch verb (write_many, N>1) stays un-pinned — that's the deferred range case.
        return make_channel_uri(channel_id, sid, sample_offset=offsets[0] if n == 1 else None)

    def _publish(
        self,
        channel_id: str,
        wire: pa.RecordBatch | None,
        samples: list[ChannelSample] | None,
    ) -> None:
        """Best-effort live fan-out + local-index feed after the durable append.

        The durable segment already holds the data; nothing here can risk it. The
        relay (push) drops on overflow (live = from-now); the index lags. This is
        the dumb-tickerplant tail — append to the log already happened; this just
        publishes.
        """
        if wire is not None and self._has_batch_subs(channel_id):
            self._notify_batch(channel_id, wire)
        if samples is not None and (self._subscribers.get(channel_id) or self._global_subscribers):
            for sample in samples:
                self._notify(channel_id, sample)
        if wire is not None and self._flight_location is not None:
            if self._push_relay is not None:
                self._push_relay.publish((channel_id, wire))
            else:
                self._flight_push_batch(channel_id, wire)
        if self._index is not None and samples is not None:
            self._index.extend_pending([self._index.index_row(channel_id, s) for s in samples])

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
        sample_offset: int | None = None,
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
            sample_offset: Internal cursor — pin the result to the one row at
                this sample_offset (used by materialization to follow a
                single-sample ticket). NOT exposed on the public
                ``channels.query`` verb; ``sample_offset`` stays a store-internal
                addressing cursor.
        """
        if self._index is not None:
            return self._index.query(
                channel_id,
                session_id=session_id,
                start=start,
                end=end,
                last_n=last_n,
                max_points=max_points,
                sample_offset=sample_offset,
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
            # Buffered-but-unflushed batches (read-after-write before the flush)
            pending = writer.pending_table()
            if pending is not None:
                tables.append(pending)

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

        # Pin to a single sample_offset (internal ticket follow). sample_offset is
        # per-sample within (session, channel); session filtering above scoped it.
        if sample_offset is not None and "sample_offset" in result.column_names:
            result = result.filter(
                [o == sample_offset for o in result.column("sample_offset").to_pylist()]
            )

        # Limit to last N
        if last_n is not None and len(result) > last_n:
            result = result.slice(len(result) - last_n)

        # LTTB decimation
        if max_points is not None and len(result) > max_points:
            result = _decimate_table(result, max_points)

        return result

    def query_registry(self) -> pa.Table:
        """All ``(hostname, channel, session)`` registry version rows (empty off
        the indexed daemon)."""
        if self._index is not None:
            return self._index.query_registry()
        cols = ChannelIndex._REGISTRY_COLUMNS.split(",")
        return pa.table({c.strip(): [] for c in cols})

    def ingest_batch(self, channel_id: str, batch: pa.RecordBatch) -> None:
        """Daemon do_put path: fan out the live batch + feed the index (no segment
        persist — Opt 1: the daemon keeps no second segment copy). Live rows reach
        the index only via its pending buffer, never the disk scan, so there is no
        overlap to dedup.
        """
        # Fan out the whole batch ONCE (cross-process relay) before any index
        # work — no re-explosion to per-sample deliveries.
        self._notify_batch(channel_id, batch)

        # Keep the registry row's last_updated fresh for a live (low-rate) channel
        # whose single segment hasn't closed yet for the scan to pick up. The row
        # was established by do_put's absorb_descriptor; one host per session, so
        # (channel, session) keys the bump.
        if self._index is not None and batch.num_rows and "received_at" in batch.schema.names:
            sids = batch.column("session_id").to_pylist()
            recv = [t for t in batch.column("received_at").to_pylist() if t is not None]
            if sids and sids[0] and recv:
                self._index.bump_last_updated(channel_id, sids[0], max(recv))

        desc = self._index.descriptor(channel_id) if self._index is not None else None
        is_scalar = desc is not None and desc.data_type.startswith("scalar:")
        has_sample_subs = bool(self._subscribers.get(channel_id) or self._global_subscribers)
        # Columnar fast path: for a scalar channel with no per-sample in-process
        # subscriber, the wire ``value`` (JSON utf8) is already the index
        # encoding, so the index projects the batch's columns straight into its
        # schema — no per-row decode / object build / re-encode loop.
        if is_scalar and not has_sample_subs:
            if self._index is not None:
                self._index.insert_live_columnar(batch)
            return

        # Array/struct channels (or a per-sample subscriber attached) need the
        # envelope split / per-sample callback, so build rows the per-row way.
        rows: list[dict[str, Any]] = []
        for i in range(batch.num_rows):
            sample = batch_row_to_sample(batch, i)
            if self._index is not None:
                rows.append(self._index.index_row(channel_id, sample))
            if has_sample_subs:
                self._notify(channel_id, sample)
        if self._index is not None:
            self._index.extend_pending(rows)

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

    def _flight_push_batch(self, channel_id: str, batch: pa.RecordBatch) -> None:
        """Push a pre-built multi-row wire batch to the Flight daemon via do_put.

        The batched-transport core: one ``write_batch`` = one gRPC message for
        the whole batch, on the held per-channel stream. Non-fatal: data is in
        IPC files, daemon rebuilds on restart.
        """
        location = self._flight_location
        if location is None:
            return
        try:
            client = self._flight_client
            if client is None:
                client = flight.connect(location)
                self._flight_client = client
            writer = self._flight_writers.get(channel_id)
            if writer is None:
                descriptor = flight.FlightDescriptor.for_command(
                    CHANNELS_PUT_COMMAND,
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

    def _push_flush(self, channel_id: object, items: list[tuple[str, pa.RecordBatch]]) -> None:
        """Flush one channel's coalesced burst down the held do_put (the dumb
        relay's transport). The shared ``PushRelay`` groups by channel and bounds
        the burst; this concatenates the per-channel wire batches — pure
        transport batching, ONE do_put per channel — and never inspects or
        rebuilds a sample. A failed push is non-fatal (the segment is durable)."""
        batches = [batch for _, batch in items]
        wire = (
            batches[0]
            if len(batches) == 1
            else pa.Table.from_batches(batches).combine_chunks().to_batches()[0]
        )
        self._flight_push_batch(str(channel_id), wire)

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
    def options(self) -> ChannelOptions:
        """The producer-local data options this store writes under."""
        return self._options

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
        return self._push_relay.dropped if self._push_relay is not None else 0

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

        Position 2 (item 4b): emits one :class:`ChannelEnded`
        (``reason="session_ended"``) per channel that received at least
        one write in this session, paired with the ``ChannelStarted``
        that fired on first write. Idempotent — second close() emits
        nothing.
        """
        # Position 2: emit ChannelEnded for every channel touched in
        # this session — before tearing down Flight / writers so the
        # event log captures the lifecycle marker while the event log
        # is still live.
        if not self._closed and self._event_log is not None:
            for channel_id in list(self._registry):
                self._event_log.emit(
                    ChannelEnded(
                        session_id=self._session_id,
                        run_id=self._channel_run_ids.get(channel_id),
                        channel_id=channel_id,
                        reason="session_ended",
                    )
                )
        self._closed = True

        # Stop the async pusher (if any) and let it drain before tearing down
        # the writers it uses.
        if self._push_relay is not None:
            self._push_relay.close()
            self._push_relay = None

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

            if self._index is not None:
                self._index.close()
        finally:
            self._writers.clear()
            self._subscribers.clear()
            self._global_subscribers.clear()
