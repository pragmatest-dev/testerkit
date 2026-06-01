"""Arrow Flight client for cross-process channel access.

Provides the same write/subscribe API as ChannelStore but over Flight RPC.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data.channels.models import (
    ChannelDescriptor,
    ChannelSample,
    batch_row_to_sample,
    encode_value,
    sample_schema,
)


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

    def write(
        self,
        channel_id: str,
        value: object,
        *,
        source: str = "remote",
        units: str | None = None,
        sample_interval: float | None = None,
        sampled_at: datetime | None = None,
    ) -> None:
        """Write a value to a remote channel via do_put.

        ``sampled_at`` (build item 11) is the optional hardware-side
        acquisition timestamp; ``None`` when the remote producer
        doesn't know.
        """
        value_str = encode_value(value)
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
            },
            schema=schema,
        )
        descriptor = flight.FlightDescriptor.for_command(channel_id.encode("utf-8"))
        writer, _ = self._client.do_put(descriptor, schema)
        writer.write_batch(batch)
        writer.close()

    def on_channel(
        self,
        channel_id: str,
        callback: Callable[[ChannelSample], None],
    ) -> Callable[[], None]:
        """Subscribe to live channel data via do_get.

        Spawns a reader thread. Returns an unsubscribe callable.
        """
        stop = threading.Event()

        def _reader() -> None:
            try:
                ticket = flight.Ticket(channel_id.encode("utf-8"))
                reader = self._client.do_get(ticket)
                for chunk in reader:
                    if stop.is_set() or self._stop.is_set():
                        break
                    batch = chunk.data
                    for i in range(batch.num_rows):
                        callback(batch_row_to_sample(batch, i))
            except (OSError, pa.ArrowException):
                if not stop.is_set() and not self._stop.is_set():
                    raise

        thread = threading.Thread(target=_reader, daemon=True, name=f"channel-sub-{channel_id}")
        thread.start()
        self._reader_threads.append(thread)

        def unsub() -> None:
            stop.set()

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
        # Always include "?" so server distinguishes historical from live
        ticket_str = channel_id + "?" + "&".join(params)
        ticket = flight.Ticket(ticket_str.encode("utf-8"))
        reader = self._client.do_get(ticket)
        return reader.read_all()

    def channels(self) -> list[ChannelDescriptor]:
        """List available channels via list_flights."""
        result = []
        for fi in self._client.list_flights():
            cid = fi.descriptor.command.decode("utf-8")
            result.append(ChannelDescriptor(channel_id=cid))
        return result

    def close(self) -> None:
        """Stop all reader threads and close the connection."""
        self._stop.set()
        for t in self._reader_threads:
            t.join(timeout=2.0)
        self._reader_threads.clear()
        self._client.close()
