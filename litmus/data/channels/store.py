"""Channel store — streaming Arrow IPC with live subscriptions.

Producers write directly via write(). Stores data in per-channel Arrow IPC
files with flexible per-channel schemas inferred from the first write.
Supports live in-process subscriptions via on_channel().
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.ipc as ipc

from litmus.data.channels.models import (
    SCALAR_SCHEMA,
    ChannelDescriptor,
    ChannelSample,
    _infer_schema,
)
from litmus.data.ref import classify_value, make_channel_uri

_WRITE_ERRORS = (OSError, pa.ArrowException)  # type: ignore[attr-defined]


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
            area = abs(
                (float(j) - px) * (avg_y - py)
                - (avg_x - px) * (values[j] - py)
            )
            if area > best_area:
                best_area = area
                best_idx = j
        # Ensure strictly increasing indices for flat signals
        if best_idx <= prev_idx:
            best_idx = prev_idx + 1
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


class _ChannelWriter:
    """Manages streaming IPC output for a single channel."""

    __slots__ = (
        "channel_id", "data_type", "schema", "_path_template",
        "_writer", "_buffer", "_flush_threshold", "_row_count",
        "_segment", "_closed_paths",
    )

    def __init__(
        self,
        channel_id: str,
        data_type: str,
        schema: pa.Schema,
        path: Path,
        flush_threshold: int = 100,
    ) -> None:
        self.channel_id = channel_id
        self.data_type = data_type
        self.schema = schema
        # Template: /dir/channel_id_session.arrow → segments append _NNN
        self._path_template = path
        self._writer: ipc.RecordBatchFileWriter | None = None
        self._buffer: list[dict] = []
        self._flush_threshold = flush_threshold
        self._row_count = 0
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
        """All closed segment paths (readable by ipc.open_file)."""
        return list(self._closed_paths)

    def _ensure_writer(self) -> ipc.RecordBatchFileWriter:
        if self._writer is None:
            p = self.path
            p.parent.mkdir(parents=True, exist_ok=True)
            self._writer = ipc.new_file(str(p), self.schema)
        return self._writer

    def append(self, row: dict) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self._flush_threshold:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        writer = self._ensure_writer()
        batch = pa.record_batch(
            {col: [r[col] for r in self._buffer] for col in self.schema.names},
            schema=self.schema,
        )
        writer.write_batch(batch)
        self._row_count += len(self._buffer)
        self._buffer.clear()
        # Rotate: close this segment so it's readable, open next on demand
        self._rotate()

    def _rotate(self) -> None:
        """Close current IPC file so it's readable, bump segment counter."""
        if self._writer is not None:
            self._closed_paths.append(self.path)
            self._writer.close()
            self._writer = None
            self._segment += 1

    def close(self) -> int:
        """Flush remaining buffer and close writer. Returns total row count."""
        self.flush()
        return self._row_count


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
        channels_dir: Path,
        session_id: UUID,
        flush_threshold: int = 100,
        *,
        serve: bool = False,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self._channels_dir = channels_dir
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

    def open(self) -> None:
        self._channels_dir.mkdir(parents=True, exist_ok=True)
        if self._serve:
            self._connect_or_serve()

    def write(
        self,
        channel_id: str,
        value: object,
        *,
        units: str | None = None,
        sample_interval: float | None = None,
        source: str = "observe",
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

        Returns:
            ``channel://`` URI pointing to this data in the store.

        Raises:
            ValueError: If value classifies as "blob" (not storable).
        """
        vtype = classify_value(value)
        if vtype == "blob":
            raise ValueError(
                f"Channel {channel_id}: value type {type(value).__name__} is not numeric. "
                "Use file:// refs for non-numeric data."
            )

        now = datetime.now(UTC)

        data_type, row, sample = self._to_arrow_row(
            channel_id, value, now, source,
            units=units, sample_interval=sample_interval,
        )
        if row is None:
            raise ValueError(f"Channel {channel_id}: could not classify value")

        # Register channel
        if channel_id not in self._registry:
            self._registry[channel_id] = ChannelDescriptor(
                channel_id=channel_id,
                data_type=data_type,
                units=units,
                first_seen=now,
            )

        # Get or create writer (schema inferred from first value)
        if channel_id not in self._writers:
            schema = _infer_schema(self._normalize_value(value, sample_interval))
            session_short = str(self._session_id)[:8]
            today = date.today().isoformat()
            path = self._channels_dir / today / f"{channel_id}_{session_short}.arrow"
            self._writers[channel_id] = _ChannelWriter(
                channel_id, data_type, schema, path, self._flush_threshold,
            )

        self._writers[channel_id].append(row)

        # Notify subscribers
        if sample is not None:
            self._notify(channel_id, sample)

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

        if isinstance(normalized, dict):
            row: dict = {"timestamp": timestamp, **normalized, "source_method": source}
            data_type = "struct"
            sample_value = normalized
        elif isinstance(normalized, bool):
            row = {"timestamp": timestamp, "value": normalized, "source_method": source}
            data_type = "scalar_bool"
            sample_value = normalized
        elif isinstance(normalized, str):
            row = {"timestamp": timestamp, "value": normalized, "source_method": source}
            data_type = "scalar_str"
            sample_value = normalized
        elif isinstance(normalized, (int, float)):
            float_value = float(normalized)
            row = {"timestamp": timestamp, "value": float_value, "source_method": source}
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
        """Notify channel and global subscribers."""
        for cb in self._subscribers.get(channel_id, []):
            try:
                cb(sample)
            except Exception as exc:
                warnings.warn(
                    f"Channel subscriber failed on '{channel_id}': {exc}",
                    stacklevel=2,
                )
        for cb in self._global_subscribers:
            try:
                cb(sample)
            except Exception as exc:
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
        start: datetime | None = None,
        end: datetime | None = None,
        last_n: int | None = None,
        max_points: int | None = None,
    ) -> pa.Table:
        """Query channel data, merging flushed files + in-memory buffer.

        Works mid-session (reads from buffer) and post-session (reads from files).

        Args:
            channel_id: Channel to query.
            start: Filter rows after this time.
            end: Filter rows before this time.
            last_n: Return only the last N rows.
            max_points: Downsample to at most this many rows using LTTB
                (Largest Triangle Three Buckets). Preserves peaks and valleys
                for faithful visual representation. Applied after all other
                filters. Requires a ``value`` or ``samples`` column.
        """
        writer = self._writers.get(channel_id)

        # Determine schema: from active writer, or try reading from disk
        schema: pa.Schema | None = writer.schema if writer else None

        tables: list[pa.Table] = []

        # Closed segment files from the active writer (already readable)
        active_paths: set[Path] = set()
        if writer:
            for seg_path in writer.all_paths:
                active_paths.add(seg_path)
                try:
                    seg_reader = ipc.open_file(str(seg_path))
                    tables.append(seg_reader.read_all())
                except Exception:
                    pass
            # Unflushed buffer
            if writer._buffer:
                buf_table = pa.table(
                    {col: [r[col] for r in writer._buffer]
                     for col in writer.schema.names},
                    schema=writer.schema,
                )
                tables.append(buf_table)

        # Read from closed files on disk (other sessions)
        for arrow_file in sorted(self._channels_dir.glob(f"*/{channel_id}_*.arrow")):
            if arrow_file in active_paths:
                continue
            try:
                reader = ipc.open_file(str(arrow_file))
                file_table = reader.read_all()
                tables.append(file_table)
                if schema is None:
                    schema = file_table.schema
            except Exception:
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
            start_utc = (
                start.astimezone(UTC) if start and start.tzinfo
                else start.replace(tzinfo=UTC) if start else None
            )
            end_utc = (
                end.astimezone(UTC) if end and end.tzinfo
                else end.replace(tzinfo=UTC) if end else None
            )
            keep = [
                (not start_utc or ts >= start_utc)
                and (not end_utc or ts <= end_utc)
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
        from litmus.data.channels import flight_manager

        location = flight_manager.acquire(
            self._channels_dir, self._flight_host, self._flight_port,
        )
        self._flight_location = location

    def _flight_release(self) -> None:
        """Release our ref on the Flight server daemon."""
        from litmus.data.channels import flight_manager

        flight_manager.release(self._channels_dir)

    @property
    def flight_location(self) -> str | None:
        """The gRPC location of the Flight server, if running."""
        return self._flight_location

    def close(self) -> None:
        """Flush all writers, write channel registry, close."""
        # Release Flight server ref
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
                        existing[cid] = json.loads(desc.model_dump_json())
                    registry_path.write_text(json.dumps(existing, indent=2))
                except _WRITE_ERRORS as exc:
                    warnings.warn(
                        f"ChannelStore failed to write registry: {exc}",
                        stacklevel=2,
                    )
        finally:
            self._writers.clear()
            self._subscribers.clear()
            self._global_subscribers.clear()
