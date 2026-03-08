"""Arrow Flight server for cross-process channel streaming.

Wraps a ChannelStore instance. Handles:
- do_put: remote writes → store.write() → persist + fan-out
- do_get: subscribe → stream batches as they arrive
- list_flights: enumerate active channels
- get_flight_info: channel schema + metadata
"""

from __future__ import annotations

import queue
import threading
import warnings
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data.channels.models import ChannelSample

if __name__ != "__main__":
    from litmus.data.channels.store import ChannelStore


class ChannelFlightServer(flight.FlightServerBase):
    """Arrow Flight server for cross-process channel streaming."""

    def __init__(
        self,
        store: ChannelStore,
        location: str = "grpc://127.0.0.1:0",
    ) -> None:
        super().__init__(location)
        self._store = store
        self._lock = threading.Lock()
        # channel_id → list of queues for active do_get subscribers
        self._flight_subscribers: dict[str, list[queue.Queue[pa.RecordBatch | None]]] = {}
        # Register a global on_channel callback to fan out to Flight subscribers
        self._unsub = store.on_channel(None, self._on_sample)

    def _on_sample(self, sample: ChannelSample) -> None:
        """Fan out new samples to channel-specific and wildcard subscribers."""
        with self._lock:
            batch: pa.RecordBatch | None = None
            # Deliver to channel-specific and wildcard ("*") subscribers
            for key in (sample.channel_id, "*"):
                queues = self._flight_subscribers.get(key)
                if not queues:
                    continue
                if batch is None:
                    batch = _sample_to_batch(sample)
                dead: list[queue.Queue[pa.RecordBatch | None]] = []
                for q in queues:
                    try:
                        q.put_nowait(batch)
                    except queue.Full:
                        dead.append(q)
                for q in dead:
                    queues.remove(q)

    def do_put(
        self,
        context: flight.ServerCallContext,
        descriptor: flight.FlightDescriptor,
        reader: flight.MetadataRecordBatchReader,
        writer: flight.FlightMetadataWriter,
    ) -> None:
        """Remote producer writes data to a channel."""
        channel_id = descriptor.command.decode("utf-8")
        for chunk in reader:
            batch = chunk.data
            try:
                _write_batch_to_store(self._store, channel_id, batch)
            except Exception as exc:
                warnings.warn(
                    f"Flight do_put failed for '{channel_id}': {exc}",
                    stacklevel=2,
                )

    def do_get(
        self,
        context: flight.ServerCallContext,
        ticket: flight.Ticket,
    ) -> flight.GeneratorStream:
        """Consumer subscribes to live channel data, or queries historical."""
        raw = ticket.ticket.decode("utf-8")

        # Historical query: channel_id?start=...&end=...&max_points=...
        if "?" in raw:
            channel_id, query_str = raw.split("?", 1)
            params = parse_qs(query_str)
            kwargs: dict[str, Any] = {}
            if "max_points" in params:
                kwargs["max_points"] = int(params["max_points"][0])
            if "last_n" in params:
                kwargs["last_n"] = int(params["last_n"][0])
            if "start" in params:
                kwargs["start"] = datetime.fromisoformat(params["start"][0])
            if "end" in params:
                kwargs["end"] = datetime.fromisoformat(params["end"][0])
            table = self._store.query(channel_id, **kwargs)
            batches = table.to_batches()
            if batches:
                return flight.RecordBatchStream(table)
            return flight.GeneratorStream(table.schema, iter([]))

        # Live subscription
        channel_id = raw
        q: queue.Queue[pa.RecordBatch | None] = queue.Queue(maxsize=10_000)

        with self._lock:
            self._flight_subscribers.setdefault(channel_id, []).append(q)

        def _generate():  # type: ignore[no-untyped-def]
            try:
                while True:
                    try:
                        batch = q.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    if batch is None:
                        break
                    yield batch
            finally:
                with self._lock:
                    subs = self._flight_subscribers.get(channel_id, [])
                    try:
                        subs.remove(q)
                    except ValueError:
                        pass

        # Use a schema that covers common cases; actual batches may vary
        schema = _sample_schema()
        return flight.GeneratorStream(schema, _generate())

    def list_flights(
        self,
        context: flight.ServerCallContext,
        criteria: bytes,
    ) -> list[flight.FlightInfo]:
        """List active channels with their schemas."""
        result = []
        for cid, desc in self._store._registry.items():
            writer = self._store._writers.get(cid)
            schema = writer.schema if writer else _sample_schema()
            fi = flight.FlightInfo(
                schema,
                flight.FlightDescriptor.for_command(cid.encode("utf-8")),
                [],
                -1,
                -1,
            )
            result.append(fi)
        return result

    def get_flight_info(
        self,
        context: flight.ServerCallContext,
        descriptor: flight.FlightDescriptor,
    ) -> flight.FlightInfo:
        """Return schema and metadata for a channel."""
        channel_id = descriptor.command.decode("utf-8")
        writer = self._store._writers.get(channel_id)
        if writer is None and channel_id not in self._store._registry:
            raise flight.FlightUnavailableError(f"Unknown channel: {channel_id}")
        schema = writer.schema if writer else _sample_schema()
        return flight.FlightInfo(
            schema,
            descriptor,
            [],
            -1,
            -1,
        )

    def shutdown(self) -> None:
        """Stop the server and clean up subscribers."""
        # Signal all subscriber queues to stop
        with self._lock:
            for queues in self._flight_subscribers.values():
                for q in queues:
                    try:
                        q.put_nowait(None)
                    except queue.Full:
                        pass
            self._flight_subscribers.clear()
        self._unsub()
        super().shutdown()


def _sample_schema() -> pa.Schema:
    """Default schema for ChannelSample batches over Flight."""
    return pa.schema([
        ("channel_id", pa.utf8()),
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("value", pa.utf8()),  # JSON-encoded for flexibility
        ("source_method", pa.utf8()),
        ("units", pa.utf8()),
        ("sample_interval", pa.float64()),
    ])


def _sample_to_batch(sample: ChannelSample) -> pa.RecordBatch:
    """Convert a ChannelSample to a single-row RecordBatch."""
    import json

    value_str = json.dumps(sample.value) if not isinstance(sample.value, str) else sample.value
    return pa.record_batch(
        {
            "channel_id": [sample.channel_id],
            "timestamp": [sample.timestamp],
            "value": [value_str],
            "source_method": [sample.source_method],
            "units": [sample.units or ""],
            "sample_interval": [sample.sample_interval],
        },
        schema=_sample_schema(),
    )


def _write_batch_to_store(
    store: ChannelStore,
    channel_id: str,
    batch: pa.RecordBatch,
) -> None:
    """Write a Flight batch into the store, row by row."""
    import json

    for i in range(batch.num_rows):
        value_raw = batch.column("value")[i].as_py()
        try:
            value = json.loads(value_raw)
        except (json.JSONDecodeError, TypeError):
            value = value_raw

        source = ""
        if "source_method" in batch.schema.names:
            source = batch.column("source_method")[i].as_py() or ""

        kwargs: dict[str, object] = {"source": source}
        if "units" in batch.schema.names:
            units = batch.column("units")[i].as_py()
            if units:
                kwargs["units"] = units
        if "sample_interval" in batch.schema.names:
            si = batch.column("sample_interval")[i].as_py()
            if si is not None:
                kwargs["sample_interval"] = si

        store.write(channel_id, value, **kwargs)  # type: ignore[arg-type]


def start_server_background(
    store: ChannelStore,
    location: str = "grpc://127.0.0.1:0",
) -> tuple[ChannelFlightServer, str]:
    """Start a ChannelFlightServer in a background thread.

    Returns (server, actual_location) where actual_location includes the
    OS-assigned port if port was 0.
    """
    server = ChannelFlightServer(store, location)
    actual_location = f"grpc://127.0.0.1:{server.port}"
    thread = threading.Thread(target=server.serve, daemon=True, name="channel-flight")
    thread.start()
    return server, actual_location
