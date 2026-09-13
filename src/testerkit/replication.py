"""Store-and-forward replication surface.

The public half of TesterKit's bench → central-server replication: read a bench's
durable event WAL past a cursor, and ingest those events into another data dir's
event store exactly-once. A forwarder is a thin loop over these two verbs.

Two verbs:

* :func:`read_segments` — read complete event batches from a data dir's WAL
  segments past a per-writer cursor. The one sanctioned direct reader of the raw
  Arrow IPC files (everything else reads through the daemon index + Query API).
* :func:`ingest_replicated` — ingest replicated events into a receiving data dir:
  mark them replicated (so they ride the receiver's terminal fence rather than
  being rejected as post-seal revival), do_put them into the receiver's events
  daemon (returning the per-batch :class:`BatchDisposition`), and dual-write them
  to the receiver's WAL for rebuild-durability. A caller advances its cursor on
  ``inserted`` + ``deduped`` only, never on ``rejected_ids``.

Identity is preserved end to end: ``id`` (the dedup key), ``occurred_at`` (source
time), ``writer_key`` and ``event_offset`` are carried verbatim. ``received_at`` and
``event_number`` are re-stamped by the receiving daemon — both are per-daemon-local
by definition, so re-stamping them is correct, not lossy.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc as ipc

from testerkit.data import duckdb_manager
from testerkit.data._duckdb_flight_server import BatchDisposition, FlightPutStream
from testerkit.data._ipc_writer import read_ipc_batches
from testerkit.data.event_log import _IPC_SCHEMA, EVENT_LOG_SCHEMA_VERSION
from testerkit.data.events import EVENT_CATALOG_VERSION

#: The Arrow IPC schema of an event WAL segment (envelope columns + typed payload
#: columns), carrying the two version stamps in its metadata. A replicated segment
#: written by :func:`ingest_replicated` uses this schema so the receiving daemon's
#: version dispatch accepts it.
EVENT_WAL_SCHEMA = _IPC_SCHEMA

# Replicated WAL segments land under this subdir of the receiving events dir; it
# matches the daemon's ``*/*.arrow`` ingest glob, so the background ingest picks
# them up (deduped against the do_put'd rows by ``id``).
_REPLICATED_SUBDIR = "_replicated"


def read_segments(events_dir: Path, *, cursor: dict[str, int] | None = None) -> pa.Table | None:
    """Read complete event batches from the WAL segments under ``events_dir``,
    keeping only rows past ``cursor`` (``{writer_key: last_event_offset}``).

    Returns a single Arrow table of the new events ordered by
    ``(writer_key, event_offset)`` — the columns a caller needs to compute the
    advanced cursor — or ``None`` if there is nothing new. A torn tail on the
    currently-appended segment is tolerated: only complete batches are returned
    (see :func:`~testerkit.data._ipc_writer.read_ipc_batches`), so the incomplete
    final record is simply picked up on the next read.
    """
    cursor = cursor or {}
    tables: list[pa.Table] = []
    # Segments live one directory deep: ``events_dir/<date-or-_replicated>/*.arrow``.
    for seg in sorted(events_dir.glob("*/*.arrow")):
        table = read_ipc_batches(seg)
        if table is not None and table.num_rows:
            tables.append(table)
    if not tables:
        return None

    combined = pa.concat_tables(tables)
    writer_keys = combined.column("writer_key").to_pylist()
    offsets = combined.column("event_offset").to_pylist()
    keep = [
        off is not None and off > cursor.get(str(wk), -1)
        for wk, off in zip(writer_keys, offsets, strict=True)
    ]
    if not any(keep):
        return None
    new_rows = combined.filter(keep)
    # Stable order for deterministic forwarding + cursor advancement.
    return new_rows.sort_by([("writer_key", "ascending"), ("event_offset", "ascending")])


def _stamp_replicated(table: pa.Table) -> pa.Table:
    """Return ``table`` with ``replicated: true`` set in every row's ``json``
    payload, so the receiving terminal fence exempts these re-ingested events from
    the post-seal producer-revival rule (same mechanism as ``derived``)."""
    payloads = table.column("json").to_pylist()
    stamped: list[str] = []
    for raw in payloads:
        try:
            obj = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            obj = {}
        obj["replicated"] = True
        stamped.append(json.dumps(obj))
    idx = table.schema.get_field_index("json")
    return table.set_column(idx, "json", pa.array(stamped, pa.string()))


def _write_wal_segment(events_dir: Path, table: pa.Table) -> None:
    """Dual-write: persist the replicated events to the receiving WAL so they
    survive an index rebuild (the on-disk daemon index alone is discarded on a
    projection-fingerprint change). Written with :data:`EVENT_WAL_SCHEMA` so the
    version stamps are present; the daemon's background ingest reads it and dedups
    by ``id`` against the do_put'd rows."""
    dest_dir = events_dir / _REPLICATED_SUBDIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Atomic publish: write to a temp name, then rename into place, so the ingest
    # glob never sees a half-written segment.
    final = dest_dir / f"{uuid4()}.arrow"
    tmp = final.with_suffix(".arrow.part")
    aligned = table.cast(EVENT_WAL_SCHEMA)
    with pa.OSFile(str(tmp), "wb") as sink, ipc.new_stream(sink, EVENT_WAL_SCHEMA) as writer:
        writer.write_table(aligned)
    tmp.rename(final)


def ingest_replicated(data_dir: Path, table: pa.Table) -> BatchDisposition:
    """Ingest replicated events into the event store under ``data_dir``.

    Marks the events replicated (fence-exempt), do_puts them into the receiving
    events daemon (returning the aggregate :class:`BatchDisposition` from the
    per-batch acks), and dual-writes them to the receiving WAL for
    rebuild-durability. ``id``-keyed dedup makes the whole operation idempotent, so
    a resend after a crash is safe.

    The disposition is authoritative for cursor advancement: advance on
    ``inserted`` + ``deduped`` only, never on ``rejected_ids``.
    """
    if table.num_rows == 0:
        return BatchDisposition()

    events_dir = data_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    stamped = _stamp_replicated(table)

    # do_put first, WAL second. do_put returns the true inserted/deduped split
    # (the rows aren't in the index yet); writing the WAL first would let the
    # daemon's background ingest land them via the file and report every row as
    # deduped. The events are already durable in the on-disk index after the
    # do_put; the WAL segment (re-read and deduped by the background ingest) adds
    # recovery across a projection-fingerprint rebuild, which discards the index.
    location = duckdb_manager.acquire(events_dir)
    put = FlightPutStream(
        location, "events", "events", reacquire=lambda: duckdb_manager.acquire(events_dir)
    )
    try:
        for batch in stamped.to_batches():
            put.write(batch)
        dispositions = put.drain()
    finally:
        put.close()

    _write_wal_segment(events_dir, stamped)

    total = BatchDisposition()
    for d in dispositions:
        total.inserted += d.inserted
        total.deduped += d.deduped
        total.rejected_ids.extend(d.rejected_ids)
    return total


__all__ = [
    "EVENT_CATALOG_VERSION",
    "EVENT_LOG_SCHEMA_VERSION",
    "EVENT_WAL_SCHEMA",
    "BatchDisposition",
    "ingest_replicated",
    "read_segments",
]
