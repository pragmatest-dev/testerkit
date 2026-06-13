"""Arrow Flight server for cross-process channel streaming.

Wraps a ChannelStore instance. Handles:
- do_put: remote writes → store.ingest_batch() → warm index + fan-out
- do_get: subscribe → stream batches as they arrive; or at-rest query
- list_flights: enumerate active channels
- get_flight_info: channel schema + metadata
"""

from __future__ import annotations

import collections
import threading
import warnings
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data.channels.models import (
    SubscribePolicy,
    sample_schema,
)

if TYPE_CHECKING:
    from litmus.data.channels.store import ChannelStore


class _SubscriberRing:
    """Per-subscriber bounded ring of batches with a drain-coalesce read.

    Replaces the raw queue + per-sample re-explosion. The whole received batch
    is put once; ``drain`` returns ALL queued batches at once so a lagging
    consumer catches up in one read (LMAX batching effect). On overflow it drops
    + counts a gap instead of removing the subscriber.
    """

    def __init__(self, policy: SubscribePolicy = SubscribePolicy.ALL, maxsize: int = 1024) -> None:
        self._policy = policy
        self._max = maxsize
        self._batches: collections.deque[pa.RecordBatch] = collections.deque()
        self._cond = threading.Condition()
        self._gaps = 0
        self._closed = False

    @property
    def gaps(self) -> int:
        """Batches dropped on overflow (consumer fell behind)."""
        return self._gaps

    def put(self, batch: pa.RecordBatch) -> None:
        with self._cond:
            if self._closed:
                return
            if self._policy is SubscribePolicy.LATEST:
                self._batches.clear()  # conflate to newest
                self._batches.append(batch)
            else:
                self._batches.append(batch)
                while len(self._batches) > self._max:
                    self._batches.popleft()
                    self._gaps += 1
            self._cond.notify()

    def drain(self, timeout: float) -> list[pa.RecordBatch] | None:
        """Block up to ``timeout`` for batches. Returns all queued batches, an
        empty list on timeout, or None once closed and drained."""
        with self._cond:
            if not self._batches and not self._closed:
                self._cond.wait(timeout)
            if self._closed and not self._batches:
                return None
            out = list(self._batches)
            self._batches.clear()
            return out

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


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
        # channel_id → list of subscriber rings for active do_get subscribers
        self._flight_subscribers: dict[str, list[_SubscriberRing]] = {}
        # Relay whole batches (no per-sample re-explosion) to Flight subscribers
        self._unsub = store.on_batch(None, self._relay_batch)

    def _relay_batch(self, channel_id: str, batch: pa.RecordBatch) -> None:
        """Put the whole received batch on each subscriber's ring, once —
        channel-specific and wildcard. No per-row work, no subscriber removal."""
        with self._lock:
            for key in (channel_id, "*"):
                for ring in self._flight_subscribers.get(key, []):
                    ring.put(batch)

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
        ring = _SubscriberRing()  # policy=ALL; Phase 3 wires policy from the ticket

        with self._lock:
            self._flight_subscribers.setdefault(channel_id, []).append(ring)

        def _generate():  # type: ignore[no-untyped-def]
            try:
                while True:
                    batches = ring.drain(1.0)
                    if batches is None:
                        break  # closed
                    if not batches:
                        continue  # timeout — keep waiting
                    # Coalesce everything queued into ONE batch: a lagging
                    # consumer catches up in a single read.
                    combined = pa.Table.from_batches(batches).combine_chunks()
                    yield from combined.to_batches()
            finally:
                ring.close()
                with self._lock:
                    subs = self._flight_subscribers.get(channel_id, [])
                    try:
                        subs.remove(ring)
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
        """Stop the server and close subscriber rings."""
        with self._lock:
            for rings in self._flight_subscribers.values():
                for ring in rings:
                    ring.close()
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
