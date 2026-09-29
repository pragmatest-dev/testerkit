"""``testerkit forward`` — store-and-forward this bench's data to a server.

A thin loop over the public replication surface. Events forward unconditionally
(unchanged default behavior, on since the original B1 build): read complete event
batches from the local WAL past a durable cursor, POST them to a central server's authed
``/ingest/events``, and advance the cursor only on rows the server accepted. Exactly-once falls
out of the server's ``id`` dedup, so a crash-and-resume simply re-sends and de-dupes.

Channel segments and file blobs forward by DEFAULT (``--no-channels`` / ``--no-files`` to
skip; docs/22 Part B). Both use the same store-and-forward shape as events (durable local
cursor, advance only on a server-accepted POST), but since neither a channel segment nor a
file blob has a WAL-style row ``id`` to dedup by, the "cursor" is a set of already-forwarded
identifiers (segment path / file URI) rather than an offset — see
``testerkit.replication.read_closed_channel_segments`` / ``read_new_file_records``. Both
also dedup SERVER-side on their local identity — a channel segment by its ``rel_path``
(the server derives a deterministic segment key from it) and a file blob by content hash —
so a resend is an idempotent no-op, never a duplicate. See ``docs/22-channels-files-spec.md``
Part B.

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

import hashlib
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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import click
from pydantic import BaseModel, ConfigDict

from testerkit.cli.root import main
from testerkit.data._accumulator_pool import AccumulatorPool
from testerkit.data.event_store import EventStore
from testerkit.data.live_projection import LiveRunProjection
from testerkit.data.live_rows import LivePush, LivePushResponse, LiveSyncState

if TYPE_CHECKING:
    import pyarrow as pa

    from testerkit.replication import ChannelSegment, FileRecord, RunArtifact

_TOKEN_ENV = "TESTERKIT_TOKEN"
_URL_ENV = "TESTERKIT_SERVER_URL"
_MAX_BYTES_ENV = "TESTERKIT_FORWARD_MAX_BYTES"
_ARROW_CONTENT_TYPE = "application/vnd.apache.arrow.stream"
_PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
# Per-request byte budget for the events pass: a large WAL backlog is forwarded
# in chunks each ≤ this cap so a single POST can never exceed the server's
# request limit (Cloud Run ~32 MiB) and 413 forever. 16 MiB leaves headroom for
# Arrow IPC framing. Overridable via ``$TESTERKIT_FORWARD_MAX_BYTES`` / ``--max-bytes``.
_DEFAULT_MAX_BYTES = 16 * 1024 * 1024
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


def _load_channels_cursor(path: Path) -> set[str]:
    """The set of channel-segment ``rel_path`` values already forwarded."""
    return set(_load_json(path).get("sent", []))


def _save_channels_cursor(path: Path, sent: set[str]) -> None:
    _save_json(path, {"sent": sorted(sent)})


def _load_files_cursor(path: Path) -> tuple[set[str], set[str]]:
    """``(sent_uris, sent_hashes)`` — the per-record cursor (URIs already
    forwarded, never resent) plus a bandwidth-only content-hash dedup set
    (bytes already shipped once from this bench are never re-uploaded under a
    different URI; the server would just no-op dedupe them anyway)."""
    raw = _load_json(path)
    return set(raw.get("sent_uris", [])), set(raw.get("sent_hashes", []))


def _save_files_cursor(path: Path, sent_uris: set[str], sent_hashes: set[str]) -> None:
    _save_json(path, {"sent_uris": sorted(sent_uris), "sent_hashes": sorted(sent_hashes)})


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


def _resolve_max_bytes(cli_value: int | None) -> int:
    """Resolve the events-pass request byte budget:
    ``--max-bytes`` → ``$TESTERKIT_FORWARD_MAX_BYTES`` → :data:`_DEFAULT_MAX_BYTES`.
    A non-positive or unparseable value at any level falls through to the next."""
    if cli_value is not None and cli_value > 0:
        return cli_value
    env = os.environ.get(_MAX_BYTES_ENV)
    if env:
        try:
            parsed = int(env)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    return _DEFAULT_MAX_BYTES


def _to_ipc_bytes(table) -> bytes:
    import pyarrow as pa
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
) -> dict | None:
    """Forward one events pass, in ascending order, in byte-bounded chunks.

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

    cursor = _load_cursor(cursor_path) if use_cursor else {}
    table = read_segments(events_dir, cursor=cursor if use_cursor else None)
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
                _advance_cursor(cursor, read_segments(events_dir, cursor=cursor), set()),
            )
        return None
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
    import pyarrow as pa

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
    """POST one closed segment to ``/ingest/channels/{channel_id}`` (Arrow IPC body,
    same transport as events' ``/ingest/events``). ``rel_path`` — the segment's
    stable local identity — rides as a query param so the server derives a
    DETERMINISTIC segment key from it and dedups on ``(org_id, segment_key)``: a
    re-forward is an idempotent no-op, never a duplicate object. Response mirrors
    ``ingest_channel_segment``'s return ``{"segment_key", "row_count", "inserted"}``.
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


def _forward_channels_once(
    channels_dir: Path,
    cursor_path: Path,
    url: str,
    token: str,
    *,
    timeout: float,
    use_cursor: bool = True,
) -> dict | None:
    """Forward every closed channel segment not yet in the durable cursor.

    Persists the cursor after EACH accepted segment (not batched at the end):
    the cursor (the already-forwarded ``rel_path`` set) is a send-side
    optimization to avoid re-uploading, but the server ALSO dedups by the
    ``rel_path``-derived segment key (see ``ingest_channel_segment``), so a
    resend is an idempotent no-op, never a duplicate object — the same
    at-least-once + idempotent-sink shape as events/runs/files. Persisting
    per-segment bounds a crash's replay window to at most the one segment in
    flight. A raised exception (network/HTTP error) stops the pass without
    recording that segment — it re-sends next poll, same as the events path.
    """
    from testerkit.replication import read_closed_channel_segments

    sent = _load_channels_cursor(cursor_path) if use_cursor else set()
    segments = read_closed_channel_segments(channels_dir, sent)
    if not segments:
        return None
    forwarded = 0
    rows = 0
    for seg in segments:
        wire = _channel_wire_table(seg)
        disp = _post_channel_segment(
            url, token, seg.channel_id, wire, rel_path=seg.rel_path, timeout=timeout
        )
        sent.add(seg.rel_path)
        if use_cursor:
            _save_channels_cursor(cursor_path, sent)
        forwarded += 1
        rows += disp.get("row_count") or 0
    return {"segments": forwarded, "rows": rows}


# --------------------------------------------------------------------------- #
# File blobs (opt-in via --files)                                             #
# --------------------------------------------------------------------------- #


def _post_file_blob(url: str, token: str, record: FileRecord, *, timeout: float) -> dict:
    """POST one blob + its sidecar to the proposed ``/ingest/files`` endpoint
    as ``multipart/form-data`` (a "meta" JSON field + a "file" binary field —
    chosen over a base64-in-JSON envelope so large blobs don't pay a ~33%
    size inflation, and over headers-only metadata since a sidecar's
    ``attributes`` bag has no size guarantee). REVIEW NEEDED: this endpoint
    does not exist on the server yet (see module docstring); response shape
    assumed to mirror ``ingest_file_blob``'s return, ``{"content_hash",
    "inserted"}``. ``step_path`` is always ``None`` — local FileStore has no
    such field to source it from (see the forward extension's review notes).
    """
    boundary = uuid.uuid4().hex
    meta = {
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
    req = urllib.request.Request(
        url.rstrip("/") + "/ingest/files",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
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

    Content-hash addressed per docs/22 Part B: bytes already forwarded once
    from this bench (tracked in ``sent_hashes``) are never re-uploaded even
    under a different URI — the server dedupes by hash anyway, so this is a
    bandwidth optimization, not a correctness requirement. The per-URI cursor
    (``sent_uris``) is the correctness mechanism: a record is retired
    (added to ``sent_uris``) only after either a confirmed 2xx POST or a local
    hash-dedup skip, and the cursor is persisted after EACH record for the
    same crash-window reasoning as channel segments.
    """
    from testerkit.replication import read_new_file_records

    sent_uris, sent_hashes = _load_files_cursor(cursor_path) if use_cursor else (set(), set())
    records = read_new_file_records(files_dir, sent_uris)
    if not records:
        return None
    forwarded = 0
    skipped = 0
    for rec in records:
        content_hash = hashlib.sha256(rec.data).hexdigest()
        if content_hash in sent_hashes:
            sent_uris.add(rec.uri)
            if use_cursor:
                _save_files_cursor(cursor_path, sent_uris, sent_hashes)
            skipped += 1
            continue
        _post_file_blob(url, token, rec, timeout=timeout)
        sent_uris.add(rec.uri)
        sent_hashes.add(content_hash)
        if use_cursor:
            _save_files_cursor(cursor_path, sent_uris, sent_hashes)
        forwarded += 1
    return {"files": forwarded, "skipped_dupe": skipped}


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
    )
    if disp is not None:
        result["events"] = disp
    if channels:
        cdisp = _forward_channels_once(
            channels_dir, channels_cursor_path, url, token, timeout=timeout, use_cursor=use_cursor
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
            lease_due = silent_for is not None and silent_for >= _LIVE_LEASE_S
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
    live: bool,
):
    """Forward this bench's data artifacts to a central server (store-and-forward).

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
