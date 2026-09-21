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

Two more READ-ONLY verbs (docs/36 P2) support forwarding a bench's compacted
lake artifacts — one finished run's Parquet, and that run's own compacted
events slice — to the central server's ``/ingest/runs`` (a proposed,
not-yet-real endpoint; see ``testerkit.cli.forward_cmd``'s module docstring,
same REVIEW-NEEDED status as ``/ingest/channels``/``/ingest/files``):

* :func:`read_new_run_artifacts` — the sanctioned direct reader of finished
  run Parquet files (already Parquet at rest — no transcode needed for these).
* :func:`read_run_events` — a one-shot, non-incremental read of every WAL
  event belonging to one ``run_id`` (not cursor-based like :func:`read_segments`
  — a finished run's event history is read once, in full, at forward time).
* :func:`run_events_segment_key` — the compacted-events-artifact's dedup
  identity: a deterministic hash of the ``(writer_key, offset-range)`` slices
  the artifact actually covers, NOT a per-event ``id`` set (docs/36 P2:
  "events dedup by segment key... retire per-event dedup reliance").
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

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


# --------------------------------------------------------------------------- #
# Run Parquet artifacts — read-only forwarding surface (docs/36 P2)           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunArtifact:
    """One finished run's Parquet artifact, ready to forward.

    ``(run_id, content_hash)`` together are the durable identity a caller's
    ledger tracks — NOT ``path`` alone. A local re-materialization (``#64``
    RE-HYDRATE: a late real ``run.ended`` supersedes an earlier synthetic-abort
    materialization — see ``_runs_duckdb_daemon._rehydrate_and_supersede``)
    overwrites the SAME file path with DIFFERENT bytes (``started_at`` is
    unchanged, so ``_run_parquet_filename`` picks the same name), so a
    path-only ledger would wrongly treat the re-materialized run as
    already-sent. Keying on content hash instead means the re-materialized
    run is recognized as genuinely new-to-forward — the local analogue of the
    server's own ``run_id``+hash supersede policy (docs/36 §7.6 G3).
    """

    run_id: str
    content_hash: str
    path: Path
    table: pa.Table


def read_new_run_artifacts(
    runs_dir: Path, sent: set[tuple[str, str]] | frozenset[tuple[str, str]]
) -> list[RunArtifact]:
    """Read every finished run Parquet under ``runs_dir`` (the runs daemon's
    own ``<data_dir>/runs/runs/<date>/*.parquet`` layout — see
    ``ParquetBackend``'s ``data_dir/"runs"`` nesting) whose ``(run_id,
    content_hash)`` pair is not already in ``sent`` (the caller's durable set
    of forwarded pairs).

    The sanctioned direct reader of run Parquet for forwarding — the runs
    analogue of :func:`read_closed_channel_segments`. Each write is atomic
    (temp file + ``os.replace`` — see ``ParquetMeasurementWriter.write_batch``
    / ``atomic_write_table``), so unlike a channel segment there is no
    "still being written" race to tolerate: a file that exists is complete.
    A file that fails to parse (read racing an in-flight ``os.replace``, or
    genuine corruption) is skipped and retried on a later poll rather than
    raised, the same tolerance every other reader here applies.
    """
    out: list[RunArtifact] = []
    for pq_path in sorted(runs_dir.glob("*/*.parquet")):
        try:
            data = pq_path.read_bytes()
        except OSError:
            continue
        try:
            table = _read_parquet_bytes(data)
        except (pa.ArrowException, OSError):
            continue
        run_id = _extract_run_id(table)
        if run_id is None:
            continue
        content_hash = hashlib.sha256(data).hexdigest()
        if (run_id, content_hash) in sent:
            continue
        out.append(RunArtifact(run_id=run_id, content_hash=content_hash, path=pq_path, table=table))
    return out


def _read_parquet_bytes(data: bytes) -> pa.Table:
    return pq.read_table(pa.BufferReader(data))


def _extract_run_id(table: pa.Table) -> str | None:
    """The run's own ``run_id`` (a single-run file — every row shares one),
    read from content rather than the filename (``_run_parquet_filename``
    only carries an 8-char run_id PREFIX — collision-prone as a real
    identifier)."""
    if "run_id" not in table.column_names:
        return None
    for v in table.column("run_id").to_pylist():
        if v:
            return str(v)
    return None


def read_run_events(events_dir: Path, run_id: str) -> pa.Table | None:
    """Every WAL event belonging to ``run_id``, across every writer/segment —
    a one-shot FULL read (not cursor-based like :func:`read_segments`): a
    finished run's event history is read once, in full, at forward time (when
    its run Parquet is discovered ready to forward), not incrementally.
    Returns ``None`` when the run has no events on this bench (already
    pruned, or a run_id that never existed here).
    """
    tables: list[pa.Table] = []
    for seg in sorted(events_dir.glob("*/*.arrow")):
        table = read_ipc_batches(seg)
        if table is not None and table.num_rows:
            tables.append(table)
    if not tables:
        return None
    combined = pa.concat_tables(tables)
    if "run_id" not in combined.column_names:
        return None
    mask = pc.equal(combined.column("run_id"), run_id)  # type: ignore[attr-defined]  # pyarrow.compute stubs omit `equal`
    filtered = combined.filter(mask)
    if filtered.num_rows == 0:
        return None
    return filtered.sort_by([("writer_key", "ascending"), ("event_offset", "ascending")])


def run_events_segment_key(run_id: str, table: pa.Table) -> str:
    """Deterministic dedup identity for a compacted per-run events artifact
    (docs/36 P2: "events dedup by segment key... retire per-event dedup
    reliance") — a hash of ``run_id`` + the sorted ``(writer_key,
    min_offset-max_offset)`` ranges the artifact actually covers, NOT a
    per-event ``id`` set.

    Two builds over the SAME slice of WAL data always produce the SAME key
    (dedup-safe without hashing row bytes). A re-materialized run's events
    artifact legitimately covers a WIDER offset range — the real terminal's
    events land at HIGHER offsets than the synthetic abort's, since they are
    appended later — so a genuine re-materialization gets a genuinely
    different key, the segment-key analogue of :class:`RunArtifact`'s
    ``(run_id, content_hash)`` pair, keyed on WAL coverage instead of file
    bytes (there is no single file to hash — the artifact is built fresh
    from possibly-multiple WAL segments each forward).
    """
    writer_keys = table.column("writer_key").to_pylist()
    offsets = table.column("event_offset").to_pylist()
    ranges: dict[str, tuple[int, int]] = {}
    for wk, off in zip(writer_keys, offsets, strict=True):
        if off is None:
            continue
        key = str(wk)
        lo, hi = ranges.get(key, (off, off))
        ranges[key] = (min(lo, off), max(hi, off))
    canonical = ",".join(f"{wk}:{lo}-{hi}" for wk, (lo, hi) in sorted(ranges.items()))
    payload = f"{run_id}|{canonical}"
    return hashlib.sha256(payload.encode()).hexdigest()


def events_table_to_parquet_bytes(table: pa.Table) -> bytes:
    """Transcode an events Arrow table to Parquet bytes (docs/36 P2: "Parquet,
    not Arrow — BigQuery can't read Arrow files"). The WAL is Arrow IPC at
    rest (the format every other reader in this module consumes); the
    compacted per-run events artifact this forwards must be Parquet, the only
    format the cloud's BigQuery-load ingest path can read."""
    import io

    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


__all__ = [
    "EVENT_CATALOG_VERSION",
    "EVENT_LOG_SCHEMA_VERSION",
    "EVENT_WAL_SCHEMA",
    "BatchDisposition",
    "ChannelSegment",
    "FileRecord",
    "RunArtifact",
    "events_table_to_parquet_bytes",
    "ingest_replicated",
    "read_closed_channel_segments",
    "read_new_file_records",
    "read_new_run_artifacts",
    "read_run_events",
    "read_segments",
    "run_events_segment_key",
]
