"""Arrow Flight server for cross-process channel streaming.

Wraps a ChannelStore instance. Handles:
- do_put: remote writes → store.ingest_batch() → warm index + fan-out
- do_get: subscribe → stream batches as they arrive; or at-rest query
- list_flights: enumerate active channels
- get_flight_info: channel schema + metadata
"""

from __future__ import annotations

import queue
import threading
import warnings
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data.channels.models import (
    ChannelSample,
    sample_schema,
    sample_to_batch,
)

if TYPE_CHECKING:
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
                    batch = sample_to_batch(sample)
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
        _context: flight.ServerCallContext,
        descriptor: flight.FlightDescriptor,
        reader: flight.MetadataRecordBatchReader,
        _writer: flight.FlightMetadataWriter,
    ) -> None:
        """Remote producer writes data to a channel."""
        channel_id = descriptor.command.decode("utf-8")
        # Absorb the descriptor from the stream schema metadata (stamped by the
        # producer on do_put open) so live channels are served their full
        # descriptor before any segment closes.
        self._store._absorb_descriptor(channel_id, reader.schema)
        for chunk in reader:
            batch = chunk.data
            try:
                self._store.ingest_batch(channel_id, batch)
            except (OSError, ValueError, pa.ArrowException) as exc:
                warnings.warn(
                    f"Flight do_put failed for '{channel_id}': {exc}",
                    stacklevel=2,
                )

    def do_get(
        self,
        _context: flight.ServerCallContext,
        ticket: flight.Ticket,
    ) -> flight.GeneratorStream:
        """Consumer subscribes to live channel data, or queries historical."""
        raw = ticket.ticket.decode("utf-8")

        # Historical query: channel_id?start=...&end=...&max_points=...
        if "?" in raw:
            channel_id, query_str = raw.split("?", 1)
            params = parse_qs(query_str)
            kwargs: dict[str, Any] = {}
            if "session_id" in params:
                kwargs["session_id"] = params["session_id"][0]
            if "max_points" in params:
                kwargs["max_points"] = int(params["max_points"][0])
            if "last_n" in params:
                kwargs["last_n"] = int(params["last_n"][0])
            if "start" in params:
                try:
                    kwargs["start"] = datetime.fromisoformat(params["start"][0])
                except ValueError as exc:
                    raise flight.FlightServerError(
                        f"Invalid 'start' timestamp: {params['start'][0]!r} ({exc})"
                    ) from exc
            if "end" in params:
                try:
                    kwargs["end"] = datetime.fromisoformat(params["end"][0])
                except ValueError as exc:
                    raise flight.FlightServerError(
                        f"Invalid 'end' timestamp: {params['end'][0]!r} ({exc})"
                    ) from exc
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
        schema = sample_schema()
        return flight.GeneratorStream(schema, _generate())

    def list_flights(
        self,
        _context: flight.ServerCallContext,
        criteria: bytes,
    ) -> list[flight.FlightInfo]:
        """List active channels with their schemas."""
        result = []
        for desc, schema in self._store.list_channel_info():
            fi = flight.FlightInfo(
                schema,
                flight.FlightDescriptor.for_command(desc.channel_id.encode("utf-8")),
                [],
                -1,
                -1,
                app_metadata=desc.model_dump_json().encode(),
            )
            result.append(fi)
        return result

    def get_flight_info(
        self,
        _context: flight.ServerCallContext,
        descriptor: flight.FlightDescriptor,
    ) -> flight.FlightInfo:
        """Return schema and metadata for a channel."""
        channel_id = descriptor.command.decode("utf-8")
        schema = self._store.get_channel_schema(channel_id)
        if schema is None:
            raise flight.FlightUnavailableError(f"Unknown channel: {channel_id}")
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


def start_server_background(
    store: ChannelStore,
    location: str = "grpc://127.0.0.1:0",
) -> tuple[ChannelFlightServer, str]:
    """Start a ChannelFlightServer in a background thread.

    Returns (server, actual_location) where actual_location includes the
    OS-assigned port if port was 0. The host is preserved from the
    input ``location`` so callers binding to a non-localhost address
    get the right URL back.
    """
    server = ChannelFlightServer(store, location)
    # Parse "grpc://host:port" → preserve host, substitute actual bound port
    parsed = urlparse(location)
    host = parsed.hostname or "127.0.0.1"
    actual_location = f"grpc://{host}:{server.port}"
    thread = threading.Thread(target=server.serve, daemon=True, name="channel-flight")
    thread.start()
    return server, actual_location
