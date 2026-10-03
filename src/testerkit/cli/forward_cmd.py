"""``testerkit forward`` — store-and-forward this bench's data to a server.

A thin loop over the public replication surface. Events forward unconditionally
(unchanged default behavior, on since the original B1 build): read complete event
batches from the local WAL past a durable cursor, POST them to a central server's authed
``/ingest/events``, and advance the cursor only on rows the server accepted. Exactly-once falls
out of the server's ``id`` dedup, so a crash-and-resume simply re-sends and de-dupes.

Channel segments and file blobs forward by DEFAULT (``--no-channels`` / ``--no-files`` to
skip; docs/22 Part B). Both use the same store-and-forward shape as events (durable local
cursor, advance only on a server-accepted POST).

Channels forward in BATCHES per stream (one channel in one session): a stream's closed
segment files are coalesced, in numeric sequence order, into one POST once they hold at
least ``--channel-flush-bytes`` or the oldest is ``--channel-flush-age`` seconds old (or on
``--once``). The cursor is a per-stream sequence high-water mark. Events hold each writer's
pending rows until ``--event-flush-bytes`` / ``--event-flush-age`` the same way. Dedup is
SERVER-side by per-stream offset high-water mark (events by writer ``event_offset``,
channels by ``sample_offset``): the server keeps only rows above what it already holds, so
a resend or a re-chunked batch lands each row once. The one rule the bench owes it is to
send each stream's rows in ascending offset order and never skip ahead. A channel batch's
``rel_path`` is a range name (``{date}/{channel}_{session8}_{lo}-{hi}.arrow``); only
pre-offset segments (null ``sample_offset``) are sent one file per POST under their real
``rel_path``. File blobs dedup server-side by URI.

Meant to run standing (systemd/container) — it is NOT a DaemonManager daemon. Auth is a
per-bench machine token in ``TESTERKIT_TOKEN``; the server URL is ``--url`` or
``TESTERKIT_SERVER_URL``.

Run Parquet forwards by DEFAULT (``--no-runs`` to skip; docs/36 P2/P3) — same
store-and-forward shape as channels/files: a durable local ledger (keyed
``(run_id, content_hash)`` — see ``testerkit.replication.RunArtifact``'s docstring for why
content hash, not path, is the identity), advance only on a server-accepted POST. A
finished run Parquet forwards as-is (no transcode — it's already Parquet at rest). (A
second, per-run compacted-events-artifact pipe — ``/ingest/runs/{run_id}/events`` — used to
ship alongside the run Parquet; it was removed 2026-09-23 as redundant with the main WAL
forward below, which already carries every run's events durably — see
docs/42-ingest-dedup-consistency.md §3.3.)

The ``/ingest/events``, ``/ingest/channels/{channel_id}``, ``/ingest/files``, and
``/ingest/runs`` endpoints this module POSTs to are LIVE on the server (docs/36 P3): the
run-Parquet path is the steady-state ingest that replaces cloud-side re-derivation, so the
at-rest results pages are populated entirely from it; the event WAL additionally feeds the
(P5) live overlay.
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import queue
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import click
import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from testerkit.cli.root import main
from testerkit.data._accumulator_pool import AccumulatorPool
from testerkit.data.event_store import EventStore
from testerkit.data.live_projection import LiveRunProjection
from testerkit.data.live_rows import LivePush, LivePushResponse, LiveSyncState
from testerkit.replication import (
    ChannelFile,
    ChannelScanner,
    ChannelSegment,
    WalScanner,
    parse_channel_segment_path,
    read_channel_file,
    select_due_writers,
)

if TYPE_CHECKING:
    from testerkit.replication import FileRecord, RunArtifact

_TOKEN_ENV = "TESTERKIT_TOKEN"
_URL_ENV = "TESTERKIT_SERVER_URL"
_MAX_BYTES_ENV = "TESTERKIT_FORWARD_MAX_BYTES"
_CHANNEL_FLUSH_BYTES_ENV = "TESTERKIT_FORWARD_CHANNEL_FLUSH_BYTES"
_CHANNEL_FLUSH_AGE_ENV = "TESTERKIT_FORWARD_CHANNEL_FLUSH_AGE"
_EVENT_FLUSH_BYTES_ENV = "TESTERKIT_FORWARD_EVENT_FLUSH_BYTES"
_EVENT_FLUSH_AGE_ENV = "TESTERKIT_FORWARD_EVENT_FLUSH_AGE"
_ARROW_CONTENT_TYPE = "application/vnd.apache.arrow.stream"
_PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
# Per-request byte budget for the events pass: a large WAL backlog is forwarded
# in chunks each ≤ this cap so a single POST can never exceed the server's
# request limit (Cloud Run ~32 MiB) and 413 forever. 16 MiB leaves headroom for
# Arrow IPC framing. Overridable via ``$TESTERKIT_FORWARD_MAX_BYTES`` / ``--max-bytes``.
_DEFAULT_MAX_BYTES = 16 * 1024 * 1024
# Batching triggers (docs: a stream flushes when its pending data reaches the byte
# threshold OR its oldest pending data reaches the age threshold OR on ``--once``).
_DEFAULT_CHANNEL_FLUSH_BYTES = 4 * 1024 * 1024
_DEFAULT_CHANNEL_FLUSH_AGE_S = 60.0
_DEFAULT_EVENT_FLUSH_BYTES = 1024 * 1024
_DEFAULT_EVENT_FLUSH_AGE_S = 60.0
# Streams forwarded in parallel (each stream stays strictly serial).
_CHANNEL_WORKERS = 4
# A segment file unreadable for longer than this, with later files behind it, is a torn
# crash remnant: skip it (loudly) rather than block its stream forever.
_UNREADABLE_SKIP_S = 600.0
# The bench's own derivation signal — dropped so the SERVER re-derives runs itself
# (forwarding it would evict the server's accumulator before it materializes).
_BENCH_LOCAL_EVENT_TYPES = frozenset({"run.materialized"})

log = logging.getLogger("testerkit.forward")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="._fwd-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_cursor(path: Path) -> dict[str, int]:
    raw = _load_json(path)
    try:
        return {str(k): int(v) for k, v in raw.items()}
    except (TypeError, ValueError):
        return {}


def _save_cursor(path: Path, cursor: dict[str, int]) -> None:
    _save_json(path, cursor)


class ChannelsCursor(BaseModel):
    """The channels forward cursor: per stream (``{date}/{channel}_{session8}``), the
    highest local segment sequence number already accepted by the server. A stream absent
    from the map starts from the first file."""

    version: Literal[2] = 2
    streams: dict[str, int] = Field(default_factory=dict)


def _migrate_v1_channels_cursor(sent: list[str]) -> ChannelsCursor:
    """Fold a v1 cursor (the set of forwarded ``rel_path`` values) into per-stream
    high-water marks: for each stream, the highest ``k`` such that every sequence
    ``0..k`` was sent. Conservative — anything above a gap is re-sent, and the server
    drops what it already holds."""
    seqs: dict[str, set[int]] = {}
    for rel in sent:
        parsed = parse_channel_segment_path(str(rel))
        if parsed is not None:
            seqs.setdefault(parsed[1], set()).add(parsed[2])
    streams: dict[str, int] = {}
    for stream, have in seqs.items():
        k = -1
        while k + 1 in have:
            k += 1
        if k >= 0:
            streams[stream] = k
    return ChannelsCursor(streams=streams)


def _load_channels_cursor(path: Path) -> ChannelsCursor:
    """Load the channels cursor, migrating a v1 ``{"sent": [...]}`` file on the fly. A
    missing or unreadable file is an empty cursor."""
    raw = _load_json(path)
    if "streams" in raw:
        try:
            return ChannelsCursor.model_validate(raw)
        except ValidationError:
            return ChannelsCursor()
    if "sent" in raw:
        return _migrate_v1_channels_cursor(list(raw.get("sent") or []))
    return ChannelsCursor()


def _save_channels_cursor(path: Path, cursor: ChannelsCursor) -> None:
    _save_json(path, cursor.model_dump(mode="json"))


def _load_files_cursor(path: Path) -> set[str]:
    """The URIs already forwarded (never resent)."""
    return set(_load_json(path).get("sent_uris", []))


def _save_files_cursor(path: Path, sent_uris: set[str]) -> None:
    _save_json(path, {"sent_uris": sorted(sent_uris)})


def _load_runs_cursor(path: Path) -> set[tuple[str, str]]:
    """``sent_runs`` — the durable ledger docs/36 P2 needs: the set of
    ``(run_id, content_hash)`` pairs whose run Parquet has already been
    forwarded (see ``RunArtifact``'s docstring for why content hash, not
    path — a re-materialized run overwrites the SAME path).

    Also reads a legacy ``sent_events`` key if present (pre-2026-09-23 cursor
    files, from the now-removed per-run events-artifact pipe) but discards
    it — nothing forwards against it anymore, and ``_save_runs_cursor`` no
    longer writes it back, so it drops out of the cursor file on the next
    save."""
    raw = _load_json(path)
    return {(str(r), str(h)) for r, h in raw.get("sent_runs", [])}


def _save_runs_cursor(path: Path, sent_runs: set[tuple[str, str]]) -> None:
    _save_json(path, {"sent_runs": sorted(sent_runs)})


def _resolve_positive(
    cli_value: float | None, env_name: str, default: float, *, cast=float
) -> float:
    """``cli_value`` → ``$env_name`` → ``default``; a non-positive or unparseable value
    at any level falls through to the next."""
    if cli_value is not None and cli_value > 0:
        return cast(cli_value)
    env = os.environ.get(env_name)
    if env:
        try:
            parsed = cast(float(env))
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    return default


def _resolve_max_bytes(cli_value: int | None) -> int:
    """Resolve the events-pass request byte budget:
    ``--max-bytes`` → ``$TESTERKIT_FORWARD_MAX_BYTES`` → :data:`_DEFAULT_MAX_BYTES`.
    A non-positive or unparseable value at any level falls through to the next."""
    return int(_resolve_positive(cli_value, _MAX_BYTES_ENV, _DEFAULT_MAX_BYTES, cast=int))


class ForwardBatchPolicy(BaseModel):
    """When a channel stream or an events writer is due to send: its pending data reaches
    the byte threshold, or its oldest pending data reaches the age threshold, or the pass
    is a ``--once`` flush. Channel bytes are Arrow IPC on disk (a segment file is already
    an IPC stream); event bytes are the writer's pending rows serialized as IPC."""

    model_config = ConfigDict(frozen=True)

    channel_flush_bytes: int = _DEFAULT_CHANNEL_FLUSH_BYTES
    channel_flush_age_s: float = _DEFAULT_CHANNEL_FLUSH_AGE_S
    event_flush_bytes: int = _DEFAULT_EVENT_FLUSH_BYTES
    event_flush_age_s: float = _DEFAULT_EVENT_FLUSH_AGE_S

    @classmethod
    def resolve(
        cls,
        *,
        channel_flush_bytes: int | None = None,
        channel_flush_age: float | None = None,
        event_flush_bytes: int | None = None,
        event_flush_age: float | None = None,
    ) -> ForwardBatchPolicy:
        """Each knob: CLI flag → its ``$TESTERKIT_FORWARD_*`` variable → the default."""
        return cls(
            channel_flush_bytes=int(
                _resolve_positive(
                    channel_flush_bytes,
                    _CHANNEL_FLUSH_BYTES_ENV,
                    _DEFAULT_CHANNEL_FLUSH_BYTES,
                    cast=int,
                )
            ),
            channel_flush_age_s=_resolve_positive(
                channel_flush_age, _CHANNEL_FLUSH_AGE_ENV, _DEFAULT_CHANNEL_FLUSH_AGE_S
            ),
            event_flush_bytes=int(
                _resolve_positive(
                    event_flush_bytes, _EVENT_FLUSH_BYTES_ENV, _DEFAULT_EVENT_FLUSH_BYTES, cast=int
                )
            ),
            event_flush_age_s=_resolve_positive(
                event_flush_age, _EVENT_FLUSH_AGE_ENV, _DEFAULT_EVENT_FLUSH_AGE_S
            ),
        )


def _to_ipc_bytes(table) -> bytes:
    import pyarrow.ipc as ipc

    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, table.schema) as w:
        w.write_table(table)
    return sink.getvalue().to_pybytes()


def _post_ingest(url: str, token: str, body: bytes, *, timeout: float) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/ingest/events",
        data=body,
        method="POST",
        headers={"Content-Type": _ARROW_CONTENT_TYPE, "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — our own server URL
        return json.loads(resp.read().decode("utf-8"))


def _advance_cursor(cursor: dict[str, int], table, rejected_ids: set[str]) -> dict[str, int]:
    """Advance each writer's cursor to the highest event_offset the server ACCEPTED
    (rows whose id was not rejected). Rejected rows never advance the cursor."""
    writer_keys = table.column("writer_key").to_pylist()
    offsets = table.column("event_offset").to_pylist()
    ids = table.column("id").to_pylist()
    out = dict(cursor)
    for wk, off, eid in zip(writer_keys, offsets, ids, strict=True):
        if off is None or str(eid) in rejected_ids:
            continue
        key = str(wk)
        if off > out.get(key, -1):
            out[key] = off
    return out


def _forward_once(
    events_dir: Path,
    cursor_path: Path,
    url: str,
    token: str,
    *,
    timeout: float,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    use_cursor: bool = True,
    policy: ForwardBatchPolicy | None = None,
    flush_all: bool = False,
    clock: Callable[[], float] = time.time,
    scanner: WalScanner | None = None,
) -> dict | None:
    """Forward one events pass, in ascending order, in byte-bounded chunks.

    Each writer's pending rows are HELD until they reach ``policy.event_flush_bytes`` or
    the oldest pending row (by ``occurred_at``) is ``policy.event_flush_age_s`` old, or
    ``flush_all`` is set (``--once``). A due writer sends all its pending rows; a writer
    that is not due sends nothing and its cursor stays put, so offsets are never skipped.
    ``scanner`` carries the WAL file-skip cache across passes.

    Instead of one POST of everything-past-cursor (which 413s a large backlog
    forever), the new rows are sliced into chunks each ≤ ``max_bytes`` and the
    cursor is saved after EACH accepted chunk — so a crash or a mid-drain HTTP
    failure persists all prior chunks' progress and only the un-acked tail
    re-sends next pass. A small delta that fits the budget is a single chunk =
    a single POST, so steady-state liveness is unchanged.

    ``use_cursor=False`` (the ``--no-cursor`` stateless mode) reads the FULL set
    (``cursor=None``) and never reads or writes the cursor file — correctness
    then rests entirely on the server's ``id`` dedup. The chunking still applies,
    so a full re-forward of a big WAL can't 413 either.
    """
    from testerkit.replication import chunk_table_by_bytes, read_segments

    policy = policy or ForwardBatchPolicy()
    cursor = _load_cursor(cursor_path) if use_cursor else {}
    table = read_segments(events_dir, cursor=cursor if use_cursor else None, scanner=scanner)
    if table is None or table.num_rows == 0:
        return None
    # Drop the bench's own derivation signals so the server re-derives fresh.
    et = table.column("event_type").to_pylist()
    keep = [t not in _BENCH_LOCAL_EVENT_TYPES for t in et]
    if not all(keep):
        table = table.filter(keep)
    if table.num_rows == 0:
        # Nothing but bench-local events past the cursor — still advance past them
        # (but only when we own a cursor; --no-cursor never writes one).
        if use_cursor:
            _save_cursor(
                cursor_path,
                _advance_cursor(
                    cursor, read_segments(events_dir, cursor=cursor, scanner=scanner), set()
                ),
            )
        return None
    due = select_due_writers(
        table,
        now=clock(),
        min_bytes=policy.event_flush_bytes,
        max_age_s=policy.event_flush_age_s,
        flush_all=flush_all,
    )
    if due is None:
        return None
    table = due
    # Chunk the ORDERED, already-filtered table so each POST body ≤ max_bytes and
    # each chunk advances the cursor monotonically per writer_key (see
    # ``chunk_table_by_bytes``). Advance from the FILTERED chunk (never a raw one)
    # so a rejected row is never leap-frogged by a later bench-local offset.
    agg: dict = {"inserted": 0, "deduped": 0, "rejected_ids": []}
    for chunk in chunk_table_by_bytes(table, max_bytes=max_bytes):
        body = _to_ipc_bytes(chunk)
        if len(body) > max_bytes:
            # One row-batch alone exceeds the budget: send it as its own request
            # (never drop it, never spin) and let the server decide. Rare — a WAL
            # segment is a couple MiB against a multi-MiB budget.
            log.warning(
                "events chunk of %d row(s) serializes to %d bytes, over the "
                "%d-byte budget; forwarding as a single oversized request",
                chunk.num_rows,
                len(body),
                max_bytes,
            )
        disp = _post_ingest(url, token, body, timeout=timeout)
        rejected = {str(x) for x in disp.get("rejected_ids", [])}
        cursor = _advance_cursor(cursor, chunk, rejected)
        if use_cursor:
            _save_cursor(cursor_path, cursor)
        agg["inserted"] += disp.get("inserted") or 0
        agg["deduped"] += disp.get("deduped") or 0
        agg["rejected_ids"].extend(disp.get("rejected_ids", []))
    return agg


# --------------------------------------------------------------------------- #
# Channel segments (opt-in via --channels)                                    #
# --------------------------------------------------------------------------- #

# Envelope columns a closed segment carries alongside its payload (mirrors
# ``ChannelIndex._INDEX_ENVELOPE`` — kept as its own copy here since that one
# is a private implementation detail of the index, not a shared constant).
_SEGMENT_ENVELOPE = frozenset(
    {
        "received_at",
        "sampled_at",
        "source_method",
        "session_id",
        "sample_interval",
        "sample_offset",
    }
)


def _channel_wire_table(segment: ChannelSegment) -> pa.Table:
    """Build the wire table for ``/ingest/channels/{channel_id}`` — testerkit's
    REAL channel-segment shape (docs/25 re-alignment): the same columns
    ``ChannelIndex`` reads — ``received_at, sampled_at, value, source_method,
    session_id, sample_interval, sample_offset`` — carrying the segment's
    ``ChannelDescriptor`` in the Arrow schema metadata so the server catalog can
    read ``value_type``/``units`` without a registry lookup. The server stores
    this shape as-is and windows it on ``received_at``. ``channel_id`` is NOT a
    column — it rides in the ingest URL.

    ``value`` typing: a scalar/array channel already has a native ``value``
    column — passed through unchanged. A struct channel (no ``value`` column; its
    fields spread across top-level columns) folds its non-envelope fields into
    one JSON-encoded ``value`` string, exactly as ``ChannelIndex`` encodes them
    at rest, so the server's ``decode_value_column`` round-trips it.
    """
    from testerkit.data.channels.models import encode_value

    table = segment.table
    n = table.num_rows
    names = table.column_names

    def _col(name, typ):  # noqa: ANN001, ANN202 — pa arrays, local helper
        return table.column(name) if name in names else pa.array([None] * n, type=typ)

    if "value" in names:
        value_col = table.column("value")
    else:
        rows = table.to_pylist()
        payloads = [{k: v for k, v in r.items() if k not in _SEGMENT_ENVELOPE} for r in rows]
        value_col = pa.array([encode_value(p) for p in payloads], type=pa.utf8())

    wire = pa.table(
        {
            "received_at": _col("received_at", pa.timestamp("us", tz="UTC")),
            "sampled_at": _col("sampled_at", pa.timestamp("us", tz="UTC")),
            "value": value_col,
            "source_method": _col("source_method", pa.utf8()),
            "session_id": _col("session_id", pa.utf8()),
            "sample_interval": _col("sample_interval", pa.float64()),
            "sample_offset": _col("sample_offset", pa.int64()),
        }
    )
    # Preserve the ChannelDescriptor (+ any other) segment metadata so the server
    # can read value_type/units at ingest — single-sourced with the local index.
    meta = table.schema.metadata
    if meta:
        wire = wire.replace_schema_metadata(meta)
    return wire


def _post_channel_segment(
    url: str, token: str, channel_id: str, table: pa.Table, *, rel_path: str, timeout: float
) -> dict:
    """POST one channel batch to ``/ingest/channels/{channel_id}`` (Arrow IPC body,
    same transport as events' ``/ingest/events``). The server dedups chained rows by the
    stream's ``sample_offset`` high-water mark, not by ``rel_path``: it keeps only rows
    above what it already holds, so a resend or a differently-chunked batch lands each row
    once. ``rel_path`` is required by the route and is used only for audit and for
    pre-offset (null ``sample_offset``) segments, where it is the segment's real local
    path. Response mirrors ``ingest_channel_segment``'s return
    ``{"segment_key", "row_count", "inserted"}``.
    """
    body = _to_ipc_bytes(table)
    endpoint = (
        url.rstrip("/")
        + f"/ingest/channels/{urllib.parse.quote(channel_id, safe='')}"
        + "?rel_path="
        + urllib.parse.quote(rel_path, safe="")
    )
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": _ARROW_CONTENT_TYPE, "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — our own server URL
        return json.loads(resp.read().decode("utf-8"))


@dataclass
class _ChannelBatch:
    """One POST's worth of a stream: the files read (in sequence order), the last
    sequence consumed (including empty and skipped files) and whether it is a lone
    pre-offset file."""

    files: list[ChannelFile] = field(default_factory=list)
    tables: list[pa.Table] = field(default_factory=list)
    hi_seq: int | None = None
    legacy: bool = False


@dataclass
class _StreamOutcome:
    """What one stream's worker did: the new high-water mark, counts, and the error (if
    any) that stopped it. Progress before an error is kept."""

    hwm: int
    files: int = 0
    posts: int = 0
    rows: int = 0
    error: Exception | None = None


def _has_sample_offsets(table: pa.Table) -> bool:
    """True when every row carries a non-negative ``sample_offset`` (so the server can
    chain it); pre-offset local data has none."""
    if "sample_offset" not in table.column_names:
        return False
    col = table.column("sample_offset")
    if col.null_count:
        return False
    return all(o >= 0 for o in col.to_pylist())


def _pending_files(files: list[ChannelFile], hwm: int) -> list[ChannelFile]:
    return files[bisect.bisect_right(files, hwm, key=lambda f: f.seq) :]


def _stream_due(
    pending: list[ChannelFile], *, policy: ForwardBatchPolicy, now: float, flush_all: bool
) -> bool:
    """A stream is due on ``--once``, when its oldest pending file's mtime is at least
    ``channel_flush_age_s`` old, or when its pending files total at least
    ``channel_flush_bytes`` (decided from stat, without opening any file)."""
    if not pending:
        return False
    if flush_all or now - pending[0].mtime >= policy.channel_flush_age_s:
        return True
    return sum(f.size for f in pending) >= policy.channel_flush_bytes


def _assemble_channel_batch(
    pending: list[ChannelFile], *, max_bytes: int, now: float
) -> _ChannelBatch:
    """Read ``pending`` files in sequence order into one batch of about ``max_bytes``.

    Stops at the first unreadable file (it is almost always the newest, mid-flush, and
    skipping it would let a higher offset overtake it). Only a file unreadable for over
    ``_UNREADABLE_SKIP_S`` with later files behind it is skipped, with a warning. A
    pre-offset file travels alone: it ends the batch before it, or is the whole batch."""
    batch = _ChannelBatch()
    size = 0
    for i, f in enumerate(pending):
        table = read_channel_file(f)
        if table is None:
            if now - f.mtime > _UNREADABLE_SKIP_S and i + 1 < len(pending):
                log.warning(
                    "skipping channel segment %s: unreadable for over %d s (torn or corrupt)",
                    f.rel_path,
                    int(_UNREADABLE_SKIP_S),
                )
                continue
            break
        if table.num_rows == 0:
            batch.hi_seq = f.seq
            continue
        if not _has_sample_offsets(table):
            if batch.files:
                break
            batch.files, batch.tables, batch.hi_seq, batch.legacy = [f], [table], f.seq, True
            break
        batch.files.append(f)
        batch.tables.append(table)
        batch.hi_seq = f.seq
        size += f.size
        if size >= max_bytes:
            break
    return batch


def _post_channel_batch(
    batch: _ChannelBatch, *, url: str, token: str, timeout: float
) -> tuple[dict, int]:
    """POST one assembled batch; returns ``(response, rows_sent)``. A chained batch goes
    under the range name ``{stream}_{lo:06d}-{hi:06d}.arrow``; a pre-offset file goes
    under its own ``rel_path``."""
    first, last = batch.files[0], batch.files[-1]
    wires = [
        _channel_wire_table(ChannelSegment(f.channel_id, f.rel_path, t))
        for f, t in zip(batch.files, batch.tables, strict=True)
    ]
    wire = wires[0] if len(wires) == 1 else pa.concat_tables(wires, promote_options="default")
    if batch.legacy:
        rel_path = first.rel_path
    else:
        rel_path = f"{first.stream}_{first.seq:06d}-{last.seq:06d}.arrow"
    disp = _post_channel_segment(
        url, token, first.channel_id, wire, rel_path=rel_path, timeout=timeout
    )
    return disp, wire.num_rows


def _forward_stream(
    files: list[ChannelFile],
    hwm: int,
    *,
    url: str,
    token: str,
    timeout: float,
    policy: ForwardBatchPolicy,
    flush_all: bool,
    clock: Callable[[], float],
) -> _StreamOutcome:
    """Send one stream's due batches strictly one after another, in ascending sequence
    order, until nothing is due or a file blocks it. Never raises: an error is returned
    with the progress made so far, so the caller can save the cursor before surfacing it."""
    out = _StreamOutcome(hwm=hwm)
    try:
        while True:
            now = clock()
            pending = _pending_files(files, out.hwm)
            if not _stream_due(pending, policy=policy, now=now, flush_all=flush_all):
                break
            batch = _assemble_channel_batch(pending, max_bytes=policy.channel_flush_bytes, now=now)
            if batch.hi_seq is None:
                break  # blocked at an unreadable first file; retry next pass
            if batch.files:
                disp, _ = _post_channel_batch(batch, url=url, token=token, timeout=timeout)
                out.posts += 1
                out.files += len(batch.files)
                out.rows += disp.get("row_count") or 0
            out.hwm = batch.hi_seq
    except Exception as exc:  # noqa: BLE001 — returned, then re-raised after the cursor save
        out.error = exc
    return out


def _forward_channels_once(  # noqa: PLR0913
    channels_dir: Path,
    cursor_path: Path,
    url: str,
    token: str,
    *,
    timeout: float,
    use_cursor: bool = True,
    policy: ForwardBatchPolicy | None = None,
    flush_all: bool = False,
    clock: Callable[[], float] = time.time,
    scanner: ChannelScanner | None = None,
) -> dict | None:
    """Forward due channel streams: batched, in numeric sequence order, up to
    ``_CHANNEL_WORKERS`` streams in parallel, each stream strictly serial.

    A stream (one channel in one session) is due when its pending segment files total
    ``policy.channel_flush_bytes``, or the oldest was written ``policy.channel_flush_age_s``
    ago, or ``flush_all`` (``--once``). Each batch is one POST. Exactly-once is the
    server's per-stream ``sample_offset`` high-water mark; the one rule owed to it is that
    a stream's rows arrive in ascending order, which the serial per-stream loop and the
    stop-at-first-unreadable-file rule guarantee.

    The cursor (``{stream: last_seq}``) is saved ONCE per pass, after every stream's
    worker returns — including on the error path, for the streams that made progress. A
    crash replays at most one pass of batches and the server drops the overlap. A failed
    stream stops for this pass only; the first error is re-raised for the caller's
    back-off.
    """
    policy = policy or ForwardBatchPolicy()
    scanner = scanner or ChannelScanner()
    cursor = _load_channels_cursor(cursor_path) if use_cursor else ChannelsCursor()
    streams = scanner.streams(channels_dir)
    now = clock()
    work: list[tuple[str, list[ChannelFile], int]] = []
    for stream, files in streams.items():
        hwm = cursor.streams.get(stream, -1)
        if _stream_due(_pending_files(files, hwm), policy=policy, now=now, flush_all=flush_all):
            work.append((stream, files, hwm))

    outcomes: list[_StreamOutcome] = []
    if work:
        with ThreadPoolExecutor(max_workers=min(_CHANNEL_WORKERS, len(work))) as pool:
            outcomes = list(
                pool.map(
                    lambda w: _forward_stream(
                        w[1],
                        w[2],
                        url=url,
                        token=token,
                        timeout=timeout,
                        policy=policy,
                        flush_all=flush_all,
                        clock=clock,
                    ),
                    work,
                )
            )

    if use_cursor:
        new_streams = {k: v for k, v in cursor.streams.items() if k in streams}
        for (stream, _, hwm), out in zip(work, outcomes, strict=True):
            if out.hwm != hwm:
                new_streams[stream] = out.hwm
        if new_streams != cursor.streams:
            _save_channels_cursor(cursor_path, ChannelsCursor(streams=new_streams))

    for out in outcomes:
        if out.error is not None:
            raise out.error
    posts = sum(o.posts for o in outcomes)
    if not posts:
        return None
    return {
        "segments": sum(o.files for o in outcomes),
        "posts": posts,
        "rows": sum(o.rows for o in outcomes),
    }


# --------------------------------------------------------------------------- #
# File blobs (opt-in via --files)                                             #
# --------------------------------------------------------------------------- #


def file_blob_multipart(record: FileRecord) -> tuple[bytes, str]:
    """The ``/ingest/files`` request for one blob + its sidecar: the
    ``multipart/form-data`` body and its Content-Type. A "meta" JSON field +
    a "file" binary field — chosen over a base64-in-JSON envelope so large
    blobs don't pay a ~33% size inflation, and over headers-only metadata
    since a sidecar's ``attributes`` bag has no size guarantee. ``meta.uri``
    is the file's identity on the server (required there); ``step_path`` is
    always ``None`` — local FileStore has no such field to source it from.
    testerkit-server's contract test posts exactly this body to its route.
    """
    boundary = uuid.uuid4().hex
    meta = {
        "uri": record.uri,
        "name": record.name,
        "mime": record.metadata.mime,
        "run_id": record.metadata.run_id,
        "session_id": record.session_id,
        "step_path": None,
        "sidecar": record.metadata.model_dump(mode="json"),
    }
    mime = record.metadata.mime or "application/octet-stream"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="meta"\r\n\r\n',
            json.dumps(meta).encode("utf-8"),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{record.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            record.data,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _post_file_blob(url: str, token: str, record: FileRecord, *, timeout: float) -> dict:
    """POST one blob + its sidecar to ``/ingest/files``; returns the server's
    ``{"uri", "content_hash", "inserted"}``."""
    body, content_type = file_blob_multipart(record)
    req = urllib.request.Request(
        url.rstrip("/") + "/ingest/files",
        data=body,
        method="POST",
        headers={"Content-Type": content_type, "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — our own server URL
        return json.loads(resp.read().decode("utf-8"))


def _forward_files_once(
    files_dir: Path,
    cursor_path: Path,
    url: str,
    token: str,
    *,
    timeout: float,
    use_cursor: bool = True,
) -> dict | None:
    """Forward every new FileStore artifact not yet in the durable cursor.

    Every URI is its own file on the server (a different URI with identical
    bytes is stored as a separate file there), so each record is POSTed once.
    A record is retired into ``sent_uris`` only after a confirmed 2xx POST, and
    the cursor is persisted after EACH record for the same crash-window
    reasoning as channel segments; a resend of the same URI is a server no-op.
    """
    from testerkit.replication import read_new_file_records

    sent_uris = _load_files_cursor(cursor_path) if use_cursor else set()
    records = read_new_file_records(files_dir, sent_uris)
    if not records:
        return None
    forwarded = 0
    for rec in records:
        _post_file_blob(url, token, rec, timeout=timeout)
        sent_uris.add(rec.uri)
        if use_cursor:
            _save_files_cursor(cursor_path, sent_uris)
        forwarded += 1
    return {"files": forwarded}


# --------------------------------------------------------------------------- #
# Run Parquet + compacted per-run events artifact (opt-in via --runs)         #
# (docs/36 P2)                                                                #
# --------------------------------------------------------------------------- #


class RunIngestResponse(BaseModel):
    """Parsed ``POST /ingest/runs`` response body (docs/42): the server
    reports what it did with this run's Parquet via ``disposition`` —
    ``"accepted"``/``"duplicate"`` are routine no-ops; ``"conflict"`` (a
    DIFFERENT file already exists for this ``run_id``) and ``"rejected"``
    (structurally invalid) mean the server kept our upload aside
    (``quarantined_as``) instead of ingesting it. Older servers omit
    ``disposition`` entirely — treated the same as ``"accepted"``. Extra
    response fields are ignored, not an error."""

    model_config = ConfigDict(extra="ignore")

    disposition: Literal["accepted", "duplicate", "conflict", "rejected"] | None = None
    run_id: str | None = None
    reason: str | None = None
    quarantined_as: str | None = None


class ForwardConflictRecord(BaseModel):
    """One append-only line of ``<data_dir>/runs/_forward_conflicts.jsonl`` —
    written whenever the server quarantines a forwarded run (``conflict`` or
    ``rejected`` disposition) so an operator can find what didn't make it in
    without digging through logs."""

    ts: datetime
    run_id: str
    disposition: Literal["conflict", "rejected"]
    local_hash: str
    reason: str | None = None
    quarantined_as: str | None = None
    server: str


def _append_forward_conflict(path: Path, record: ForwardConflictRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json())
        f.write("\n")


def _post_run_parquet(url: str, token: str, artifact: RunArtifact, *, timeout: float) -> dict:
    """POST one finished run's Parquet to the proposed ``/ingest/runs``
    endpoint (Parquet body — already Parquet at rest, no transcode). REVIEW
    NEEDED: this endpoint does not exist on the server yet (see module
    docstring; docs/36 P3 is the cloud-side acceptance work) — response shape
    assumed to be ``{"run_id", "accepted"}``-ish; only a 2xx is relied on
    here, nothing in the body is parsed by the caller.
    """
    body = artifact.path.read_bytes()
    req = urllib.request.Request(
        url.rstrip("/") + "/ingest/runs",
        data=body,
        method="POST",
        headers={"Content-Type": _PARQUET_CONTENT_TYPE, "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — our own server URL
        return json.loads(resp.read().decode("utf-8"))


def _forward_runs_once(
    runs_dir: Path,
    cursor_path: Path,
    url: str,
    token: str,
    *,
    timeout: float,
    use_cursor: bool = True,
) -> dict | None:
    """Forward every finished run Parquet not yet in the durable ledger.

    Persists the ledger after EACH run (not batched at the end) — same
    crash-window reasoning as channels/files: a raised exception mid-pass
    (network/HTTP error) stops the pass without recording that run, so it
    re-sends next poll. A resend is safe, never a duplicate — server-side
    idempotency (the ``(run_id, hash)`` supersede policy, docs/36 §7.6 G3).
    """
    from testerkit.replication import read_new_run_artifacts

    sent_runs = _load_runs_cursor(cursor_path) if use_cursor else set()
    artifacts = read_new_run_artifacts(runs_dir, sent_runs)
    if not artifacts:
        return None
    forwarded = 0
    for art in artifacts:
        raw = _post_run_parquet(url, token, art, timeout=timeout)
        resp = RunIngestResponse.model_validate(raw)
        if resp.disposition in ("conflict", "rejected"):
            log.warning(
                "run %s %s by server (not ingested): reason=%s quarantined_as=%s",
                art.run_id,
                resp.disposition,
                resp.reason,
                resp.quarantined_as,
            )
            _append_forward_conflict(
                cursor_path.parent / "_forward_conflicts.jsonl",
                ForwardConflictRecord(
                    ts=datetime.now(UTC),
                    run_id=art.run_id,
                    disposition=resp.disposition,
                    local_hash=art.content_hash,
                    reason=resp.reason,
                    quarantined_as=resp.quarantined_as,
                    server=url,
                ),
            )
        # Terminal outcome either way (accepted/duplicate/conflict/rejected):
        # the server has made its decision and quarantined what it didn't
        # keep, so there is nothing to retry — advance past it like a success.
        sent_runs.add((art.run_id, art.content_hash))
        if use_cursor:
            _save_runs_cursor(cursor_path, sent_runs)
        forwarded += 1
    return {"runs": forwarded}


def _forward_all_once(  # noqa: PLR0913
    events_dir: Path,
    events_cursor_path: Path,
    channels_dir: Path,
    channels_cursor_path: Path,
    files_dir: Path,
    files_cursor_path: Path,
    runs_dir: Path,
    runs_cursor_path: Path,
    url: str,
    token: str,
    *,
    timeout: float,
    channels: bool = True,
    files: bool = True,
    runs: bool = True,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    use_cursor: bool = True,
    policy: ForwardBatchPolicy | None = None,
    flush_all: bool = False,
    clock: Callable[[], float] = time.time,
    wal_scanner: WalScanner | None = None,
    channel_scanner: ChannelScanner | None = None,
) -> dict:
    """Run one poll pass over the enabled data-artifact stores.

    ALL stores forward by DEFAULT — events, channels, files, and runs — so a
    plain ``testerkit forward`` uploads everything available. The ``channels``/
    ``files``/``runs`` flags exist only to LIMIT a pass (``--no-channels`` etc.);
    events always forward. A store with nothing new no-ops (its
    ``_forward_*_once`` returns ``None``). Any store's failure raises out
    immediately (the caller's retry/backoff handles it); a store that already
    sent this pass has advanced its own cursor before a later store raises, so
    its progress is never lost.

    ``flush_all`` (``--once``) overrides the batching holds; ``wal_scanner`` /
    ``channel_scanner`` carry the per-pass caches across a standing loop.
    """
    result: dict[str, dict] = {}
    disp = _forward_once(
        events_dir,
        events_cursor_path,
        url,
        token,
        timeout=timeout,
        max_bytes=max_bytes,
        use_cursor=use_cursor,
        policy=policy,
        flush_all=flush_all,
        clock=clock,
        scanner=wal_scanner,
    )
    if disp is not None:
        result["events"] = disp
    if channels:
        cdisp = _forward_channels_once(
            channels_dir,
            channels_cursor_path,
            url,
            token,
            timeout=timeout,
            use_cursor=use_cursor,
            policy=policy,
            flush_all=flush_all,
            clock=clock,
            scanner=channel_scanner,
        )
        if cdisp is not None:
            result["channels"] = cdisp
    if files:
        fdisp = _forward_files_once(
            files_dir, files_cursor_path, url, token, timeout=timeout, use_cursor=use_cursor
        )
        if fdisp is not None:
            result["files"] = fdisp
    if runs:
        rdisp = _forward_runs_once(
            runs_dir,
            runs_cursor_path,
            url,
            token,
            timeout=timeout,
            use_cursor=use_cursor,
        )
        if rdisp is not None:
            result["runs"] = rdisp
    return result


# --------------------------------------------------------------------------- #
# Live channel (docs/41): best-effort push of an executing run's folded rows  #
# --------------------------------------------------------------------------- #
#
# A separate thread with its OWN EventStore subscription and its OWN
# AccumulatorPool — it shares no cursor, queue or failure mode with the durable
# passes above. A failed push is dropped (the next push carries the current
# state); it never blocks or retries the durable channel.

_LIVE_WATCHED_INTERVAL_S = 1.0
_LIVE_UNWATCHED_INTERVAL_S = 5.0
_LIVE_LEASE_S = 30.0
_LIVE_CPU_SHARE = 0.05  # projection + diff may use ~5 % of the push interval
_LIVE_TICK_S = 0.25
_LIVE_ATTACH_RETRY_S = 5.0


def _post_live(url: str, token: str, push: LivePush, *, timeout: float) -> LivePushResponse:
    """``POST /ingest/live/runs/{run_id}`` with the station token, like every other
    bench upload. A 409 body (``{finalized: true}``) is a normal response."""
    req = urllib.request.Request(
        url.rstrip("/") + f"/ingest/live/runs/{urllib.parse.quote(push.run_id, safe='')}",
        data=push.model_dump_json().encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — our own server URL
            body = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        body = exc.read()
    return LivePushResponse.model_validate_json(body)


class _LiveRun:
    """Per-run pusher bookkeeping: what the server holds, the throttle clock, the
    last response's ``watched`` bit, and the measured projection cost."""

    def __init__(self) -> None:
        self.sync = LiveSyncState()
        self.projection = LiveRunProjection()
        self.pending = False
        self.last_push: float | None = None
        self.last_header: float | None = None  # last header the server acknowledged
        self.watched = False
        self.projection_s = 0.0

    def interval(self) -> float:
        """Throttle ``T``: 1 s while watched, else 5 s; if projection + diff costs more
        than 5 % of ``T`` the effective ``T`` becomes ``20 x projection_s``."""
        base = _LIVE_WATCHED_INTERVAL_S if self.watched else _LIVE_UNWATCHED_INTERVAL_S
        return max(base, self.projection_s / _LIVE_CPU_SHARE)


class LivePusher:
    """The live channel's bench side (docs/41 §3): fold events into a pusher-owned
    pool, diff each dirty run to per-doc hashes, push only what changed.

    Per run a leading-edge throttle: push now if nothing went out in the last ``T``,
    else at ``last_push + T``; a header-only lease push after 30 s of silence.
    ``clock`` / ``perf`` / ``wall_ns`` are injectable so tests drive it with a fake
    clock; :meth:`tick` is one pass, :meth:`start` runs it on a thread.
    """

    def __init__(
        self,
        post: Callable[[LivePush], LivePushResponse],
        *,
        clock: Callable[[], float] = time.monotonic,
        perf: Callable[[], float] = time.perf_counter,
        wall_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._post = post
        self._clock = clock
        self._perf = perf
        self._wall_ns = wall_ns
        self._pool = AccumulatorPool()
        self._events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self._runs: dict[str, _LiveRun] = {}
        # Runs owed an immediate final push (RunEnded seen / local materialization done),
        # bypassing the throttle; ``_closing`` ones are evicted after it.
        self._flush: set[str] = set()
        self._closing: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsubscribe: Callable[[], None] | None = None

    # -- event intake ------------------------------------------------------

    def on_event(self, evt: dict[str, Any]) -> None:
        """EventStore subscription callback. Only enqueues: the pusher thread is the
        pool's sole reader and writer, so the fold never races the projection."""
        self._events.put(evt)

    def attach(self, event_store: EventStore) -> None:
        """Subscribe with the runs daemon's catch-up replay, so a pusher restart
        rebuilds the state of every open run."""
        self._unsubscribe = event_store.on_event(self.on_event, replay="unmaterialized_runs")

    def _drain_events(self) -> None:
        while True:
            try:
                evt = self._events.get_nowait()
            except queue.Empty:
                return
            try:
                rid = str(evt.get("run_id") or "")
                if evt.get("event_type") == "run.materialized":
                    if rid:  # one last push with the ended state, then pushing stops
                        self._closing.add(rid)
                else:
                    self._pool.dispatch(evt)
                    if rid and evt.get("event_type") == "run.ended":
                        self._flush.add(rid)
            except Exception as exc:  # noqa: BLE001 — a bad event must not kill the pusher
                log.debug("live: event dispatch failed: %s", exc)

    # -- one pass ----------------------------------------------------------

    def tick(self) -> None:
        """Fold pending events, then push every run that is due."""
        self._drain_events()
        dirty, evicted = self._pool.take_dirty()
        for rid in evicted:
            self._runs.pop(rid, None)
        for rid in dirty:
            self._runs.setdefault(rid, _LiveRun()).pending = True
        now = self._clock()
        for rid in self._flush | self._closing:
            if (run := self._runs.get(rid)) is not None:
                self._push_run(rid, run, now, lease=False)  # final state, ignoring the throttle
            if rid in self._closing:
                self._pool.evict(rid)
                self._runs.pop(rid, None)
        self._flush.clear()
        self._closing.clear()
        for rid, run in list(self._runs.items()):
            silent_for = None if run.last_push is None else now - run.last_push
            change_due = run.pending and (silent_for is None or silent_for >= run.interval())
            # The lease is a heartbeat on the HEADER (docs/41 §2.1): row pushes do not
            # renew the server's lease, so a run whose rows change every few seconds
            # still owes a header every 30 s. It keeps the throttle, so a failing
            # server is not retried every tick.
            header_age = None if run.last_header is None else now - run.last_header
            lease_due = (
                header_age is not None
                and header_age >= _LIVE_LEASE_S
                and silent_for is not None
                and silent_for >= run.interval()
            )
            if change_due or lease_due:
                self._push_run(rid, run, now, lease=lease_due)

    def _push_run(self, run_id: str, run: _LiveRun, now: float, *, lease: bool) -> None:
        acc = self._pool.get(run_id)
        if acc is None:
            self._runs.pop(run_id, None)
            return
        t0 = self._perf()
        run.projection.refresh(acc)  # projects only what changed since the last push
        if run.projection.header is None:  # no RunStarted yet
            run.pending = False
            return
        pushes = run.sync.build_pushes_from(
            run_id,
            run.projection.header,
            run.projection.docs,
            run.projection.hashes,
            now=now,
            now_ns=self._wall_ns(),
            force_header=lease,
        )
        if run.last_push is not None:  # the first pass is a one-off catch-up, not steady state
            run.projection_s = self._perf() - t0
        run.pending = False
        if not pushes:
            return
        run.last_push = now
        for push in pushes:
            try:
                resp = self._post(push)
            except Exception as exc:  # noqa: BLE001 — drop: the next push carries current state
                log.debug("live: push for %s dropped: %s", run_id, exc)
                run.pending = True
                return
            if resp.finalized:
                self._pool.evict(run_id)  # the server committed this run: stop pushing
                self._runs.pop(run_id, None)
                return
            run.watched = resp.watched
            if resp.stale:
                run.pending = True  # nothing was applied
                return
            run.sync.commit(push, now=now)
            if push.header is not None:
                run.last_header = now
            if resp.resync:  # after the commit, which clears the manifest debt
                run.sync.request_resync()

    # -- thread ------------------------------------------------------------

    def _loop(self, event_store_factory: Callable[[], EventStore] | None) -> None:
        while event_store_factory is not None and not self._stop.is_set():
            try:
                self.attach(event_store_factory())
                break
            except Exception as exc:  # noqa: BLE001 — live is optional; retry, never fail forward
                log.warning("live: cannot attach to the event store (will retry): %s", exc)
                self._stop.wait(_LIVE_ATTACH_RETRY_S)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("live: pass failed: %s", exc)
            self._stop.wait(_LIVE_TICK_S)

    def start(self, event_store_factory: Callable[[], EventStore] | None = None) -> None:
        """Run on a daemon thread; ``event_store_factory`` builds the EventStore to
        subscribe to (omit to feed :meth:`on_event` directly)."""
        self._thread = threading.Thread(
            target=self._loop, args=(event_store_factory,), name="testerkit-live-push", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._unsubscribe is not None:
            self._unsubscribe()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


@main.command()
@click.option(
    "--url",
    default=None,
    help=f"Server ingest base URL (or ${_URL_ENV}, testerkit.yaml `server.url`, "
    "or a prior `testerkit connect`)",
)
@click.option(
    "--token",
    default=None,
    help=f"Machine auth token (or ${_TOKEN_ENV}, or a prior `testerkit connect`)",
)
@click.option(
    "--data-dir",
    default=None,
    type=click.Path(),
    help="Data dir to forward (default: resolved project data dir)",
)
@click.option("--interval", default=5.0, help="Seconds between polls")
@click.option("--timeout", default=30.0, help="Per-request HTTP timeout (seconds)")
@click.option("--once", is_flag=True, help="Forward what's available, then exit")
@click.option(
    "--channels/--no-channels",
    default=True,
    help="Forward closed channel segments (ON by default; --no-channels to skip).",
)
@click.option(
    "--files/--no-files",
    default=True,
    help="Forward new file blobs + sidecars (ON by default; --no-files to skip).",
)
@click.option(
    "--runs/--no-runs",
    default=True,
    help="Forward finished run Parquet + per-run events artifacts (ON by default; "
    "--no-runs to skip).",
)
@click.option(
    "--no-cursor",
    is_flag=True,
    default=False,
    help="Stateless catch-up/re-seed: read every enabled store's FULL set and never "
    "read or write any _forward_cursor.json (correctness rests on server-side dedup). "
    "Use to re-forward everything to a fresh/alternate server the local per-data-dir "
    "cursor would otherwise skip.",
)
@click.option(
    "--max-bytes",
    default=None,
    type=int,
    help=f"Max bytes per events request (default {_DEFAULT_MAX_BYTES}, or "
    f"${_MAX_BYTES_ENV}); a large backlog is split into ascending chunks under this "
    "cap so a single POST can't exceed the server's request limit.",
)
@click.option(
    "--channel-flush-bytes",
    default=None,
    type=int,
    help=f"Send a channel stream's pending segments once they total this many bytes "
    f"(default {_DEFAULT_CHANNEL_FLUSH_BYTES}, or ${_CHANNEL_FLUSH_BYTES_ENV}).",
)
@click.option(
    "--channel-flush-age",
    default=None,
    type=float,
    help=f"Send a channel stream's pending segments once the oldest is this many seconds "
    f"old (default {_DEFAULT_CHANNEL_FLUSH_AGE_S:g}, or ${_CHANNEL_FLUSH_AGE_ENV}).",
)
@click.option(
    "--event-flush-bytes",
    default=None,
    type=int,
    help=f"Send an event writer's pending rows once they reach this many bytes "
    f"(default {_DEFAULT_EVENT_FLUSH_BYTES}, or ${_EVENT_FLUSH_BYTES_ENV}).",
)
@click.option(
    "--event-flush-age",
    default=None,
    type=float,
    help=f"Send an event writer's pending rows once the oldest is this many seconds old "
    f"(default {_DEFAULT_EVENT_FLUSH_AGE_S:g}, or ${_EVENT_FLUSH_AGE_ENV}). "
    "--once always flushes everything.",
)
@click.option(
    "--live/--no-live",
    default=True,
    help="Push executing runs' folded rows to the server's live view, best-effort on "
    "its own thread (ON by default; --no-live to skip; not used with --once). A failed "
    "push is dropped, never retried, and never blocks the durable forward.",
)
def forward(  # noqa: PLR0913
    url: str | None,
    token: str | None,
    data_dir: str | None,
    interval: float,
    timeout: float,
    once: bool,
    channels: bool,
    files: bool,
    runs: bool,
    no_cursor: bool,
    max_bytes: int | None,
    channel_flush_bytes: int | None,
    channel_flush_age: float | None,
    event_flush_bytes: int | None,
    event_flush_age: float | None,
    live: bool,
):
    """Forward this bench's data artifacts to a central server (store-and-forward).

    Channels and events are batched: a channel stream or event writer sends once its
    pending data reaches the byte threshold or its oldest data reaches the age threshold
    (the ``--channel-flush-*`` / ``--event-flush-*`` options); ``--once`` flushes everything.
    The cloud channels page and ``/events`` therefore lag the bench by up to about a
    minute; the live run view does not.

    Forwards EVERY available artifact by default — the event WAL, closed channel
    segments, new file blobs (docs/22 Part B), and finished run Parquet +
    compacted per-run events artifacts (docs/36 P2). Nobody adds a flag to get a
    full upload; the ``--no-channels`` / ``--no-files`` / ``--no-runs`` flags
    exist only to LIMIT a pass. A store with nothing new no-ops.

    URL/token resolution falls through ``--url``/``--token`` →
    ``$TESTERKIT_SERVER_URL``/``$TESTERKIT_TOKEN`` → the project ``server.url`` /
    the global credential store a prior ``testerkit connect`` wrote — so
    after ``testerkit connect``, a bare ``testerkit forward`` needs neither.
    """
    from testerkit.data.data_dir import resolve_data_dir, resolve_server_token, resolve_server_url

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    resolved_max_bytes = _resolve_max_bytes(max_bytes)
    policy = ForwardBatchPolicy.resolve(
        channel_flush_bytes=channel_flush_bytes,
        channel_flush_age=channel_flush_age,
        event_flush_bytes=event_flush_bytes,
        event_flush_age=event_flush_age,
    )
    use_cursor = not no_cursor
    server = resolve_server_url(url)
    token = resolve_server_token(token)
    if not server:
        raise click.ClickException(
            "not connected — run `testerkit connect <web-app-url>` to enroll this "
            f"machine (it stores the direct backend URL to forward to). Or pass "
            f"--url / ${_URL_ENV} / set testerkit.yaml `server.url`."
        )
    if not token:
        raise click.ClickException(
            f"a machine token is required (--token, ${_TOKEN_ENV}, or `testerkit connect`)"
        )

    resolved = resolve_data_dir(Path(data_dir) if data_dir else None)
    events_dir = resolved / "events"
    cursor_path = events_dir / "_forward_cursor.json"
    channels_dir = resolved / "channels"
    channels_cursor_path = channels_dir / "_forward_cursor.json"
    files_dir = resolved / "files"
    files_cursor_path = files_dir / "_forward_cursor.json"
    # ParquetBackend nests its own "runs" under the passed data_dir (see
    # `RunArtifact`/`retention._referenced_file_keys`'s identical convention)
    # — the runs daemon's own data dir is `resolved/"runs"`, so finished run
    # Parquet actually lands at `resolved/"runs"/"runs"/<date>/*.parquet`.
    runs_dir = resolved / "runs" / "runs"
    runs_cursor_path = resolved / "runs" / "_forward_cursor.json"
    log.info("forwarding %s → %s", events_dir, server)
    if channels:
        log.info("forwarding channel segments: %s → %s", channels_dir, server)
    if files:
        log.info("forwarding file blobs: %s → %s", files_dir, server)
    if runs:
        log.info("forwarding run Parquet: %s → %s", runs_dir, server)
    if not use_cursor:
        log.info("stateless mode (--no-cursor): full re-forward, cursor files untouched")
    if live and not once:
        # Own daemon thread, own EventStore subscription + pool (docs/41 §3.1); a
        # failure here never touches the durable passes below.
        log.info("live push: %s → %s", resolved, server)
        LivePusher(lambda push: _post_live(server, token, push, timeout=timeout)).start(
            lambda: EventStore(_data_dir=resolved)
        )

    wal_scanner = WalScanner()
    channel_scanner = ChannelScanner()
    backoff = interval
    while True:
        try:
            result = _forward_all_once(
                events_dir,
                cursor_path,
                channels_dir,
                channels_cursor_path,
                files_dir,
                files_cursor_path,
                runs_dir,
                runs_cursor_path,
                server,
                token,
                timeout=timeout,
                channels=channels,
                files=files,
                runs=runs,
                max_bytes=resolved_max_bytes,
                use_cursor=use_cursor,
                policy=policy,
                flush_all=once,
                wal_scanner=wal_scanner,
                channel_scanner=channel_scanner,
            )
            backoff = interval  # reset after a clean pass
            disp = result.get("events")
            if disp is not None:
                log.info(
                    "forwarded: inserted=%s deduped=%s rejected=%s",
                    disp.get("inserted"),
                    disp.get("deduped"),
                    len(disp.get("rejected_ids", [])),
                )
            if "channels" in result:
                log.info("forwarded channel segments: %s", result["channels"])
            if "files" in result:
                log.info("forwarded file blobs: %s", result["files"])
            if "runs" in result:
                log.info("forwarded run Parquet: %s", result["runs"])
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
            # Transient — the cursor did NOT advance, so the batch re-sends next pass.
            log.warning("forward failed (will retry): %s", exc)
            if once:
                raise click.ClickException(f"forward failed: {exc}") from exc
            time.sleep(min(backoff, 60.0))
            backoff = min(backoff * 2, 60.0)
            continue
        if once:
            return
        time.sleep(interval)
