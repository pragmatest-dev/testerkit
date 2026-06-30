"""Wire a ChannelStore onto the shared ``DuckDBFlightServer``.

Channels has no SQL-over-one-table read and no plain-insert write, so it rides
the shared server through its two extension seams:

- **do_put → put-hook** (``ingest_batch``): index the live samples + return the
  batch for per-subscriber fan-out. The wire batch carries the ``channel_id``
  column, so the shared server's per-subscription equality filter routes each
  subscriber its channel with no client-side broadcast noise.
- **do_get → query-hook**: the typed read verb (range / last-N / decimate /
  ``session_id``) plus the ``__registry__`` and ``__channels__`` discovery
  verbs — so a channels db needs no DuckDB connection on the server.

Live subscribe uses the shared per-subscription buffer with a ``channel_id``
predicate; channels registers NO ``replay_sql``, so the buffer is lossy
(drop-oldest + gap, recover from the durable segment) — the channels contract.
Client-chosen ``LATEST`` conflation rides the buffer's ``conflate`` flag.
"""

from __future__ import annotations

import threading
import warnings
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import pyarrow as pa

from litmus.data._duckdb_flight_server import DuckDBFlightServer
from litmus.data.channels.models import CHANNELS_FLIGHT_DB, sample_schema
from litmus.data.models import ensure_utc

if TYPE_CHECKING:
    from litmus.data.channels.store import ChannelStore

_REGISTRY_VERB = "__registry__"
_CHANNELS_VERB = "__channels__"
# Lock-free liveness verb: answers without touching the store/index, so a busy
# daemon (writers holding _index_lock) is never falsely declared dead. A probe
# routed through store.query would contend on _index_lock under write load and
# trigger a spurious kill-and-respawn — the channels concurrent-write collapse.
_PING_VERB = "__ping__"


def _channel_descriptors_table(store: ChannelStore) -> pa.Table:
    """Enumeration verb result: one ``descriptor`` JSON row per active channel.

    Replaces the old ``list_flights`` app_metadata carrier — the client rebuilds
    each ``ChannelDescriptor`` from the JSON.
    """
    descs = [desc for desc, _schema in store.list_channel_info()]
    return pa.table({"descriptor": pa.array([d.model_dump_json() for d in descs], type=pa.utf8())})


def _make_query_hook(store: ChannelStore):  # type: ignore[no-untyped-def]
    """do_get verb router: discovery verbs, else ``channel_id?params`` → query."""

    def hook(payload: str) -> pa.Table:
        if payload == _PING_VERB:
            return pa.table({"ok": pa.array([1], type=pa.int8())})
        if payload == _REGISTRY_VERB:
            return store.query_registry()
        if payload == _CHANNELS_VERB:
            return _channel_descriptors_table(store)
        channel_id, _, query_str = payload.partition("?")
        params = parse_qs(query_str)
        kwargs: dict[str, object] = {}
        if "session_id" in params:
            kwargs["session_id"] = params["session_id"][0]
        if "max_points" in params:
            kwargs["max_points"] = int(params["max_points"][0])
        if "last_n" in params:
            kwargs["last_n"] = int(params["last_n"][0])
        if "start" in params:
            kwargs["start"] = ensure_utc(datetime.fromisoformat(params["start"][0]))
        if "end" in params:
            kwargs["end"] = ensure_utc(datetime.fromisoformat(params["end"][0]))
        return store.query(channel_id, **kwargs)  # type: ignore[arg-type]

    return hook


def _make_put_hook(store: ChannelStore):  # type: ignore[no-untyped-def]
    """do_put handler: absorb the descriptor, index the batch.

    Returns ``None`` — fan-out is NOT driven off the hook return. Both the do_put
    path (here, via ``ingest_batch``) and an in-process ``store.write`` funnel
    through the store's ``_notify_batch``, and the ``on_batch`` bridge registered
    in :func:`register_channel_hooks` is the single fan-out into the server's
    subscribers. The producer pushes one channel per ``do_put``, so the batch is
    single-``channel_id``.
    """

    def hook(table: pa.Table) -> pa.Table | None:
        if table.num_rows == 0:
            return None
        channel_id = table.column("channel_id")[0].as_py()
        # Absorb the descriptor ONCE per channel (the bespoke server did it once
        # per stream-open). After the first batch the registry holds it, so the
        # per-batch hot path skips the metadata parse + the registry INSERT under
        # _index_lock — and keeps ingest_batch's columnar fast path alive (it
        # needs the registered scalar descriptor to take the columnar branch).
        if store._index is not None and not store._index.has(channel_id):
            store._index.absorb_descriptor(channel_id, table.schema)
        for batch in table.to_batches():
            try:
                store.ingest_batch(channel_id, batch)
            except (OSError, ValueError, pa.ArrowException) as exc:
                warnings.warn(
                    f"Channel ingest failed for {channel_id!r}: {exc}",
                    stacklevel=2,
                )
        return None

    return hook


def register_channel_hooks(server: DuckDBFlightServer, store: ChannelStore) -> None:
    """Register the channels put/query/subscribe seams on a shared server.

    The ``on_batch`` bridge is the one fan-out into the server's subscribers:
    every batch the store produces — a remote ``do_put`` (via the put-hook's
    ``ingest_batch``) or an in-process ``store.write`` — fires ``_notify_batch``,
    which the bridge relays to ``_publish``; the per-subscription ``channel_id``
    predicate then routes each subscriber its channel.
    """
    server.register_put_hook(CHANNELS_FLIGHT_DB, _make_put_hook(store))
    server.register_query_hook(CHANNELS_FLIGHT_DB, _make_query_hook(store))
    # No replay_sql → the subscribe buffer is lossy (drop-oldest + gap), the
    # channels live-tail contract. The stream yields sample_schema batches.
    server.register_subscribe_schema(CHANNELS_FLIGHT_DB, sample_schema())

    def _relay(_channel_id: str, batch: pa.RecordBatch) -> None:
        # Skip the table-build + publish when nobody is subscribed — the daemon's
        # ingest hot path (a pure-write workload has no live subscribers) pays
        # nothing for fan-out it won't do.
        if server.has_subscribers(CHANNELS_FLIGHT_DB):
            server._publish(CHANNELS_FLIGHT_DB, pa.Table.from_batches([batch]))

    store.on_batch(None, _relay)


def start_server_background(
    store: ChannelStore,
    location: str = "grpc://127.0.0.1:0",
) -> tuple[DuckDBFlightServer, str]:
    """Start a shared Flight server wired for ``store`` in a background thread.

    Returns ``(server, actual_location)`` with the OS-assigned port substituted
    when ``location`` requested port 0, preserving the requested host.
    """
    # parallel=True so concurrent producers' do_put isn't serialized on a
    # server-wide lock channels doesn't need (the store owns its index locking)
    # — matches the daemon and the bespoke server's lock-free do_put.
    server = DuckDBFlightServer(location, parallel=True)
    register_channel_hooks(server, store)
    parsed = urlparse(location)
    host = parsed.hostname or "127.0.0.1"
    actual_location = f"grpc://{host}:{server.port}"
    threading.Thread(target=server.serve, daemon=True, name="channel-flight").start()
    return server, actual_location
