"""Channel store — streaming Arrow IPC with live subscriptions.

Producers write directly via write(). Stores data in per-channel Arrow IPC
files with flexible per-channel schemas inferred from the first write.
Supports live in-process subscriptions via on_channel().
"""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.flight as flight
import pyarrow.ipc as ipc

from litmus.data._atomic import atomic_write_text
from litmus.data._ipc_writer import BufferedIPCWriter
from litmus.data.channels import flight_manager
from litmus.data.channels.models import (
    SCALAR_SCHEMA,
    ChannelDescriptor,
    ChannelSample,
    _infer_schema,
    sample_to_batch,
)
from litmus.data.ref import classify_value, make_channel_uri
from litmus.data.subscribers._output_file import OutputFile

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

    Visually lossless: preserves peaks, valleys, and shape better than
    naive stride decimation. O(n) time, no dependencies.

    Reference: Sveinn Steinarsson, "Downsampling Time Series for Visual
    Representation", MSc thesis, University of Iceland, 2013.
    """
    n = len(values)
    if n <= n_out or n_out < 3:
        return list(range(n))

    selected: list[int] = [0]  # always keep first
    bucket_size = (n - 2) / (n_out - 2)

    prev_idx = 0
    for i in range(1, n_out - 1):
        # Bucket boundaries
        b_start = int((i - 1) * bucket_size) + 1
        b_end = int(i * bucket_size) + 1
        # Next bucket average (for triangle area calculation)
        nb_start = int(i * bucket_size) + 1
        nb_end = min(int((i + 1) * bucket_size) + 1, n)
        avg_x = (nb_start + nb_end - 1) / 2.0
        avg_y = sum(values[nb_start:nb_end]) / max(nb_end - nb_start, 1)

        # Pick point in current bucket with largest triangle area
        best_idx = b_start
        best_area = -1.0
        px, py = float(prev_idx), values[prev_idx]
        for j in range(b_start, min(b_end, n)):
            area = abs((float(j) - px) * (avg_y - py) - (avg_x - px) * (values[j] - py))
            if area > best_area:
                best_area = area
                best_idx = j
        # Ensure strictly increasing indices for flat signals, but
        # clamp to the current bucket so monotonicity-driven picks
        # don't drift into the next bucket's range.
        if best_idx <= prev_idx:
            bucket_max = min(b_end, n) - 1
            best_idx = min(prev_idx + 1, bucket_max)
        selected.append(best_idx)
        prev_idx = best_idx

    selected.append(n - 1)  # always keep last
    return selected


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
    - Channel registry (JSON metadata)
    - Live in-process subscriptions via on_channel()
    - Mid-session query() merging flushed files + in-memory buffer
    """

    def __init__(
        self,
        results_dir: Path,
        session_id: UUID,
        flush_threshold: int = 100,
        *,
        serve: bool = False,
        host: str = "127.0.0.1",
        port: int = 0,
        on_output: Callable[[OutputFile], None] | None = None,
    ) -> None:
        # Parent-only convention — caller passes the results parent
        # (containing ``runs/``, ``channels/``, ``events/`` …); the
        # store owns its ``channels/`` subdir. Mirrors RunStore /
        # StepsQuery / MeasurementsQuery / EventStore.
        self._channels_dir = results_dir / "channels"
        self._session_id = session_id
        self._flush_threshold = flush_threshold
        self._writers: dict[str, _ChannelWriter] = {}
        self._registry: dict[str, ChannelDescriptor] = {}
        self._subscribers: dict[str, list[Callable[[ChannelSample], None]]] = {}
        self._global_subscribers: list[Callable[[ChannelSample], None]] = []
        self._serve = serve
        self._flight_host = host
        self._flight_port = port
        self._flight_location: str | None = None
        self._flight_client: flight.FlightClient | None = None
        self._on_output = on_output

    def open(self) -> None:
        self._channels_dir.mkdir(parents=True, exist_ok=True)
        if self._serve:
            self._connect_or_serve()

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

    def write(
        self,
        channel_id: str,
        value: object,
        *,
        units: str | None = None,
        sample_interval: float | None = None,
        source: str = "observe",
        instrument_role: str = "",
        resource: str = "",
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
                first_seen=now,
            )

        # Get or create writer (schema inferred from first value)
        if channel_id not in self._writers:
            schema = _infer_schema(self._normalize_value(value, sample_interval))
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
            # Push to Flight daemon so cross-process subscribers see it
            self._flight_push(channel_id, sample)

        return make_channel_uri(channel_id, str(self._session_id))

    @staticmethod
    def _normalize_value(
        value: object,
        sample_interval: float | None = None,
    ) -> object:
        """Normalize legacy tuple format and numpy arrays for schema inference."""
        # Legacy waveform tuple: ([samples], dt)
        if isinstance(value, (list, tuple)) and len(value) >= 1:
            first = value[0]
            if isinstance(first, (list, tuple)):
                samples = list(first)
                dt = float(value[1]) if len(value) > 1 else 0.0
                return {"samples": samples, "sample_interval": dt}

        # numpy array → list
        tolist = getattr(value, "tolist", None)
        if tolist is not None:
            return {"samples": tolist(), "sample_interval": sample_interval or 0.0}

        # Plain list of numbers → array with sample_interval
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], (int, float)):
            return {"samples": list(value), "sample_interval": sample_interval or 0.0}

        return value

    def _to_arrow_row(
        self,
        channel_id: str,
        value: object,
        timestamp: datetime,
        source: str,
        *,
        units: str | None = None,
        sample_interval: float | None = None,
    ) -> tuple[str, dict | None, ChannelSample | None]:
        """Convert a value to an Arrow-compatible row dict."""
        normalized = self._normalize_value(value, sample_interval)

        sid = str(self._session_id)

        common = {"timestamp": timestamp, "source_method": source, "session_id": sid}

        if isinstance(normalized, dict):
            row: dict = {**common, **normalized}
            # A normalized dict with a ``samples`` list is a waveform
            # capture (the result of ``_normalize_value`` folding an
            # array / tuple / numpy ndarray into ``{samples,
            # sample_interval}``). Tag it accordingly so the registry
            # carries the precise shape, not the generic ``struct``
            # used for arbitrary structured records.
            if isinstance(normalized.get("samples"), list):
                data_type = "waveform"
            else:
                data_type = "struct"
            sample_value = normalized
        elif isinstance(normalized, bool):
            row = {**common, "value": normalized}
            data_type = "scalar_bool"
            sample_value = normalized
        elif isinstance(normalized, str):
            row = {**common, "value": normalized}
            data_type = "scalar_str"
            sample_value = normalized
        elif isinstance(normalized, (int, float)):
            float_value = float(normalized)
            row = {**common, "value": float_value}
            data_type = "scalar"
            sample_value = float_value
        else:
            warnings.warn(
                f"Channel {channel_id}: cannot store {type(value).__name__}",
                stacklevel=3,
            )
            return "scalar", None, None

        sample = ChannelSample(
            channel_id=channel_id,
            timestamp=timestamp,
            value=sample_value,
            units=units,
            sample_interval=sample_interval,
            source_method=source,
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

        # Filter by time range
        if start is not None or end is not None:
            timestamps = result.column("timestamp").to_pylist()
            start_utc = _to_utc(start)
            end_utc = _to_utc(end)
            keep = [
                (not start_utc or ts >= start_utc) and (not end_utc or ts <= end_utc)
                for ts in timestamps
            ]
            result = result.filter(keep)

        # Limit to last N
        if last_n is not None and len(result) > last_n:
            result = result.slice(len(result) - last_n)

        # LTTB decimation
        if max_points is not None and len(result) > max_points:
            result = _decimate_table(result, max_points)

        return result

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
            descriptor = flight.FlightDescriptor.for_command(
                channel_id.encode("utf-8"),
            )
            writer, _ = client.do_put(descriptor, batch.schema)
            writer.write_batch(batch)
            writer.close()
        except (OSError, RuntimeError, pa.ArrowException) as exc:
            warnings.warn(f"Channel Flight push failed (non-fatal): {exc}", stacklevel=2)
            self._flight_client = None

    @property
    def flight_location(self) -> str | None:
        """The gRPC location of the Flight server, if running."""
        return self._flight_location

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
        """Flush all writers, write channel registry, close."""
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

            # Write channel registry
            if self._registry:
                try:
                    registry_path = self._channels_dir / "_registry.json"
                    # Merge with existing registry
                    existing: dict[str, dict] = {}
                    if registry_path.exists():
                        existing = json.loads(registry_path.read_text())
                    for cid, desc in self._registry.items():
                        existing[cid] = desc.model_dump(mode="json")
                    atomic_write_text(json.dumps(existing, indent=2), registry_path)
                except _WRITE_ERRORS as exc:
                    warnings.warn(
                        f"ChannelStore failed to write registry: {exc}",
                        stacklevel=2,
                    )
            # Notify transport for each written channel file
            if self._on_output:
                for writer in self._writers.values():
                    for ipc_path in writer.all_paths:
                        try:
                            self._on_output(
                                OutputFile(
                                    path=ipc_path,
                                    format="channels",
                                )
                            )
                        except Exception as exc:  # noqa: BLE001 — callback isolation
                            warnings.warn(
                                f"Channel on_output callback failed for {ipc_path}: {exc}",
                                stacklevel=2,
                            )
        finally:
            self._writers.clear()
            self._subscribers.clear()
            self._global_subscribers.clear()
