"""Arrow Flight client for cross-process channel access.

Provides the same write/subscribe API as ChannelStore but over Flight RPC.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data._flight_query import call_options
from litmus.data._flight_subscribe import subscribe
from litmus.data.channels import flight_manager
from litmus.data.channels.models import (
    CHANNELS_FLIGHT_DB,
    CHANNELS_PUT_COMMAND,
    ChannelDescriptor,
    ChannelSample,
    SubscribePolicy,
    batch_row_to_sample,
    encode_value,
    sample_schema,
)


def _subscribe_ticket(channel_id: str, policy: SubscribePolicy) -> flight.Ticket:
    """Build a shared-server subscribe ticket.

    ``channels\\0__SUBSCRIBE__\\0channel_id=<id>`` — the server-side equality
    filter routes only this channel's rows to the subscriber (no client-side
    broadcast). The ``"*"`` wildcard subscribes to every channel: it sends NO
    ``channel_id`` predicate (empty filter = all rows). ``LATEST`` adds
    ``&conflate=latest`` (the gauge that keeps only the newest batch).
    """
    parts = [] if channel_id == "*" else [f"channel_id={quote(channel_id)}"]
    if policy is SubscribePolicy.LATEST:
        parts.append("conflate=latest")
    return flight.Ticket(f"{CHANNELS_FLIGHT_DB}\0__SUBSCRIBE__\0{'&'.join(parts)}".encode())


class ChannelClient:
    """Connect to a ChannelStore Flight server.

    Provides write() and on_channel() matching ChannelStore's API,
    but communicating over Arrow Flight RPC.
    """

    def __init__(self, location: str = "grpc://localhost:8815") -> None:
        self._location = location
        self._client = flight.connect(location)
        self._reader_threads: list[threading.Thread] = []
        self._stop = threading.Event()
        # Per-channel monotonic write position — this remote producer stamps
        # the same sample_offset the in-process ChannelStore does, so the daemon
        # carries it unchanged into both the relayed live batch and the index.
        self._channel_seq: dict[str, itertools.count] = {}

    def write(
        self,
        channel_id: str,
        value: object,
        *,
        source: str = "remote",
        units: str | None = None,
        sample_interval: float | None = None,
        sampled_at: datetime | None = None,
        session_id: str | None = None,
    ) -> None:
        """Write a value to a remote channel via do_put.

        ``sampled_at`` (build item 11) is the optional hardware-side
        acquisition timestamp; ``None`` when the remote producer
        doesn't know. ``session_id`` attributes the sample to a session
        in the daemon's index; ``None`` for sessionless writes.
        """
        value_str = encode_value(value)
        seq = next(self._channel_seq.setdefault(channel_id, itertools.count()))
        schema = sample_schema()
        batch = pa.record_batch(
            {
                "channel_id": [channel_id],
                "received_at": [datetime.now(UTC)],
                "sampled_at": [sampled_at],
                "value": [value_str],
                "source_method": [source],
                "units": [units or ""],
                "sample_interval": [sample_interval],
                "session_id": [session_id],
                "sample_offset": [seq],
            },
            schema=schema,
        )
        descriptor = flight.FlightDescriptor.for_command(CHANNELS_PUT_COMMAND)
        writer, _ = self._client.do_put(descriptor, schema, options=call_options())
        writer.write_batch(batch)
        writer.close()

    def on_channel(
        self,
        channel_id: str,
        callback: Callable[[ChannelSample], None],
        *,
        policy: SubscribePolicy = SubscribePolicy.ALL,
    ) -> Callable[[], None]:
        """Subscribe to live channel data via do_get (per-sample callback).

        ``policy=LATEST`` conflates to the newest sample. Spawns a reader
        thread. Returns an unsubscribe callable.
        """

        def _on_batch(batch: pa.RecordBatch) -> None:
            for i in range(batch.num_rows):
                callback(batch_row_to_sample(batch, i))

        unsub, thread = subscribe(
            self._client,
            _subscribe_ticket(channel_id, policy),
            _on_batch,
            name=f"channel-sub-{channel_id}",
            client_stop=self._stop,
        )
        self._reader_threads.append(thread)
        return unsub

    def on_channel_batch(
        self,
        channel_id: str,
        callback: Callable[[pa.RecordBatch], None],
        *,
        policy: SubscribePolicy = SubscribePolicy.ALL,
    ) -> Callable[[], None]:
        """Subscribe to live channel data as whole (coalesced) batches.

        Each batch the daemon yields is handed to ``callback`` once — the
        consumer decodes N rows columnar instead of paying a per-sample
        callback. Spawns a reader thread; returns an unsubscribe callable.
        """
        unsub, thread = subscribe(
            self._client,
            _subscribe_ticket(channel_id, policy),
            callback,
            name=f"channel-sub-batch-{channel_id}",
            client_stop=self._stop,
        )
        self._reader_threads.append(thread)
        return unsub

    def query(
        self,
        channel_id: str,
        *,
        session_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_points: int | None = None,
        last_n: int | None = None,
    ) -> pa.Table:
        """Historical query via do_get with query params in ticket.

        Args:
            channel_id: Channel to query.
            session_id: If provided, only return data for this session.
            start: Filter rows after this time (zoom window start).
            end: Filter rows before this time (zoom window end).
            max_points: Downsample to at most this many rows (LTTB).
            last_n: Return only the last N rows.
        """
        params: list[str] = []
        if session_id is not None:
            params.append(f"session_id={quote(session_id)}")
        if start is not None:
            params.append(f"start={quote(start.isoformat())}")
        if end is not None:
            params.append(f"end={quote(end.isoformat())}")
        if max_points is not None:
            params.append(f"max_points={max_points}")
        if last_n is not None:
            params.append(f"last_n={last_n}")
        # ``channels\0<channel_id>?params`` routes to the query-hook verb.
        payload = channel_id + "?" + "&".join(params)
        ticket = flight.Ticket(f"{CHANNELS_FLIGHT_DB}\0{payload}".encode())
        reader = self._client.do_get(ticket, options=call_options())
        return reader.read_all()

    def channel_registry(self) -> pa.Table:
        """Fetch the daemon's ``(hostname, channel, session)`` registry rows.

        Unlike :meth:`channels` (one current descriptor per channel), this
        returns the full non-unique registry — one version row per session, with
        ``last_updated`` — for liveness/discovery.
        """
        ticket = flight.Ticket(f"{CHANNELS_FLIGHT_DB}\0__registry__".encode())
        reader = self._client.do_get(ticket, options=call_options())
        return reader.read_all()

    def channels(self) -> list[ChannelDescriptor]:
        """List available channels with their descriptors.

        The daemon serves the full ``ChannelDescriptor`` (units, role, …) as a
        JSON row per channel over the ``__channels__`` enumeration verb.
        """
        ticket = flight.Ticket(f"{CHANNELS_FLIGHT_DB}\0__channels__".encode())
        table = self._client.do_get(ticket, options=call_options()).read_all()
        return [
            ChannelDescriptor.model_validate_json(row)
            for row in table.column("descriptor").to_pylist()
        ]

    def close(self) -> None:
        """Stop all reader threads and close the connection."""
        self._stop.set()
        for t in self._reader_threads:
            t.join(timeout=2.0)
        self._reader_threads.clear()
        self._client.close()


@contextmanager
def channel_query_client(channels_dir: Path) -> Iterator[ChannelClient]:
    """Connect to the channels daemon for at-rest query (req 2: no client reglob).

    Acquires (spawning if needed) the singleton channels daemon — which
    owns the warm index — and yields a client bound to it. Releases the
    daemon ref on exit. Use this instead of constructing an ephemeral
    globbing ``ChannelStore`` for reads: query goes through the daemon's
    index, never a per-call disk walk.
    """
    location = flight_manager.acquire(channels_dir)
    client = ChannelClient(location)
    try:
        yield client
    finally:
        client.close()
        flight_manager.release(channels_dir)
