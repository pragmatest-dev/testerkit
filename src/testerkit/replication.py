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

Two additional READ-ONLY verbs support forwarding channel segments and file blobs
to a *different* kind of receiver — a central server's object-storage ingest
(``testerkit_server.object_ingest.ingest_channel_segment`` /
``ingest_file_blob``), not another local data dir. There is no local
``ingest_channel_*`` / ``ingest_file_*`` counterpart here because the receiving
side already lives server-side; this module only reads the bench's own stores:

* :func:`read_closed_channel_segments` — the sanctioned direct reader of
  **closed** channel segment files (never the one a producer is still writing).
* :func:`read_new_file_records` — the sanctioned direct reader of FileStore
  blobs + their sidecars, past a set of already-forwarded URIs.

Both skip a caller-supplied "already forwarded" set instead of taking an
offset-style cursor: unlike an event WAL (one growing file per writer), a
channel segment or a file blob is a whole, immutable, singly-written unit the
moment it exists — so the durable cursor a caller persists (see
``testerkit.cli.forward_cmd``) is simply the set of identifiers already sent,
not a position to resume from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc as ipc

from testerkit.data import duckdb_manager
from testerkit.data._duckdb_flight_server import BatchDisposition, FlightPutStream
from testerkit.data._ipc_writer import read_ipc_batches
from testerkit.data.event_log import _IPC_SCHEMA, EVENT_LOG_SCHEMA_VERSION
from testerkit.data.events import EVENT_CATALOG_VERSION
from testerkit.data.files.models import FileArtifactMetadata

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


# --------------------------------------------------------------------------- #
# Channel segments — read-only forwarding surface (docs/22 Part B)            #
# --------------------------------------------------------------------------- #

# Segment filename convention: ``{channel_id}_{session_short}[_NNN].arrow``
# (see ``ChannelStore._ensure_writer`` / ``_ChannelWriter.path``). Mirrors the
# identical pattern in ``ChannelStore.list_channel_refs`` and
# ``ChannelIndex._scan_disk`` — channel_id may itself contain ``_``, so the
# greedy group only works because the filename is anchored on the trailing
# 8-hex-char session_short (+ optional zero-padded rotation suffix).
_SEGMENT_NAME_RE = re.compile(r"^(.+)_([0-9a-f]{8})(?:_\d+)?$")


@dataclass(frozen=True)
class ChannelSegment:
    """One closed, complete channel segment ready to forward.

    ``rel_path`` (POSIX, relative to the channels dir) is the stable dedup
    identifier a caller's cursor tracks — a segment is written exactly once
    then closed immutable (see ``ChannelStore``/``_ChannelWriter``), so a path
    is never reused or reopened once it exists as a complete file.
    """

    channel_id: str
    rel_path: str
    table: pa.Table


def read_closed_channel_segments(
    channels_dir: Path, sent: set[str] | frozenset[str]
) -> list[ChannelSegment]:
    """Read every CLOSED channel segment under ``channels_dir`` not already in
    ``sent`` (the caller's durable set of forwarded ``rel_path`` values).

    The sanctioned direct reader of channel segment files for forwarding — the
    channels analogue of :func:`read_segments`. A channel segment is opened,
    written, and closed (EOS written) within a single flush of the producer's
    ``_ChannelWriter``, so a file that reads back as a complete Arrow IPC
    stream IS closed and will never be appended to again; one still being
    written (a rare, brief race — see ``_ChannelWriter._flush_pending``) fails
    to parse and is simply skipped, to be picked up once the write completes on
    a later poll. This is the exact tolerance ``ChannelIndex._scan_disk``
    already relies on for the same files.

    An empty segment (zero rows — shouldn't normally occur, since a segment is
    only created by a flush of buffered samples) is skipped rather than
    forwarded. Channel ids that don't match the expected filename convention
    are skipped (defensive — e.g. a stray non-segment ``.arrow`` file).
    """
    out: list[ChannelSegment] = []
    for seg in sorted(channels_dir.glob("*/*.arrow")):
        rel = seg.relative_to(channels_dir).as_posix()
        if rel in sent:
            continue
        m = _SEGMENT_NAME_RE.match(seg.stem)
        if not m:
            continue
        try:
            table = ipc.open_stream(pa.OSFile(str(seg), "rb")).read_all()
        except (pa.ArrowInvalid, OSError):
            # Still being written (or torn) — not closed yet; retry next poll.
            continue
        if table.num_rows == 0:
            continue
        out.append(ChannelSegment(channel_id=m.group(1), rel_path=rel, table=table))
    return out


# --------------------------------------------------------------------------- #
# File blobs — read-only forwarding surface (docs/22 Part B)                  #
# --------------------------------------------------------------------------- #

_FILE_SIDECAR_SUFFIX = ".meta.json"


@dataclass(frozen=True)
class FileRecord:
    """One FileStore artifact (blob bytes + its sidecar) ready to forward.

    ``uri`` (the ``file://...`` URI ``FileStore.write`` returned) is the
    stable per-record identifier a caller's cursor tracks — FileStore never
    reuses or overwrites a key once published (see ``FileStore._unique_filename``).
    """

    uri: str
    session_id: str
    name: str
    data: bytes
    metadata: FileArtifactMetadata


def read_new_file_records(files_dir: Path, sent: set[str] | frozenset[str]) -> list[FileRecord]:
    """Read every FileStore artifact under ``files_dir`` whose ``uri`` is not
    already in ``sent`` (the caller's durable set of forwarded URIs).

    The sanctioned direct reader of FileStore blobs for forwarding — the files
    analogue of :func:`read_segments`. Mirrors
    ``testerkit.data.files.catalog.scan_sidecars``'s discovery walk (glob the
    ``{date}/{session_id}/{name}.meta.json`` sidecars, resolve each to its
    blob) but reads the blob bytes back too, since a forwarder ships the
    artifact itself rather than cataloging it in place.

    A sidecar whose blob is missing, or that fails to parse/read, is skipped
    silently (defensive — the same tolerance ``scan_sidecars`` applies) and
    picked up again on a later poll once/if it resolves. Reads the whole blob
    into memory: fine for typical artifact sizes, a known limitation for very
    large files (see the forward extension's review notes).
    """
    out: list[FileRecord] = []
    for sidecar in sorted(files_dir.glob(f"*/*/*{_FILE_SIDECAR_SUFFIX}")):
        blob = sidecar.with_name(sidecar.name[: -len(_FILE_SIDECAR_SUFFIX)])
        if not blob.exists():
            continue
        session_id = blob.parent.name
        name = blob.name
        uri = f"file://{blob.parent.parent.name}/{session_id}/{name}"
        if uri in sent:
            continue
        try:
            metadata = FileArtifactMetadata.model_validate_json(sidecar.read_text())
            data = blob.read_bytes()
        except (OSError, ValueError):
            continue
        out.append(
            FileRecord(uri=uri, session_id=session_id, name=name, data=data, metadata=metadata)
        )
    return out


__all__ = [
    "EVENT_CATALOG_VERSION",
    "EVENT_LOG_SCHEMA_VERSION",
    "EVENT_WAL_SCHEMA",
    "BatchDisposition",
    "ChannelSegment",
    "FileRecord",
    "ingest_replicated",
    "read_closed_channel_segments",
    "read_new_file_records",
    "read_segments",
]
