"""``testerkit forward`` — store-and-forward this bench's data to a server.

A thin loop over the public replication surface. Events forward unconditionally
(unchanged default behavior, on since the original B1 build): read complete event
batches from the local WAL past a durable cursor, POST them to a central server's authed
``/ingest``, and advance the cursor only on rows the server accepted. Exactly-once falls
out of the server's ``id`` dedup, so a crash-and-resume simply re-sends and de-dupes.

Channel segments and file blobs forward ADDITIONALLY, opt-in via ``--channels`` /
``--files`` (docs/22 Part B) — a plain ``testerkit forward`` with neither flag behaves
exactly as before either flag existed. Both use the same store-and-forward shape as
events (durable local cursor, advance only on a server-accepted POST), but since neither
a channel segment nor a file blob has a WAL-style row ``id`` to dedup by, the "cursor" is
a set of already-forwarded identifiers (segment path / file URI) rather than an offset —
see ``testerkit.replication.read_closed_channel_segments`` /
``read_new_file_records``. Channel segments carry no server-side dedup at all (each POST
always creates a new object) and file blobs dedup server-side by content hash — see
``docs/22-channels-files-spec.md`` Part B and the module-level REVIEW notes below for
exactly what that means for exactly-once here.

Meant to run standing (systemd/container) — it is NOT a DaemonManager daemon. Auth is a
per-bench machine token in ``TESTERKIT_TOKEN``; the server URL is ``--url`` or
``TESTERKIT_URL``.

REVIEW NEEDED — the ``/ingest/channels/{channel_id}`` and ``/ingest/files`` endpoints
this module POSTs to do not exist on the server yet (testerkit-server's
``ingest_channel_segment`` / ``ingest_file_blob`` are an unwired seam — see
``testerkit_server/object_ingest.py``'s own module comment). The wire shapes below are
this side's proposal, not a confirmed contract; channel/file forwarding cannot be
end-to-end verified until the server side lands. Do not enable ``--channels``/``--files``
against a real server without confirming its endpoints match.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import click

from testerkit.cli.root import main

if TYPE_CHECKING:
    import pyarrow as pa

    from testerkit.replication import ChannelSegment, FileRecord

_TOKEN_ENV = "TESTERKIT_TOKEN"
_URL_ENV = "TESTERKIT_URL"
_ARROW_CONTENT_TYPE = "application/vnd.apache.arrow.stream"
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


def _to_ipc_bytes(table) -> bytes:
    import pyarrow as pa
    import pyarrow.ipc as ipc

    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, table.schema) as w:
        w.write_table(table)
    return sink.getvalue().to_pybytes()


def _post_ingest(url: str, token: str, body: bytes, *, timeout: float) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/ingest",
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
    events_dir: Path, cursor_path: Path, url: str, token: str, *, timeout: float
) -> dict | None:
    from testerkit.replication import read_segments

    cursor = _load_cursor(cursor_path)
    table = read_segments(events_dir, cursor=cursor)
    if table is None or table.num_rows == 0:
        return None
    # Drop the bench's own derivation signals so the server re-derives fresh.
    et = table.column("event_type").to_pylist()
    keep = [t not in _BENCH_LOCAL_EVENT_TYPES for t in et]
    if not all(keep):
        table = table.filter(keep)
    if table.num_rows == 0:
        # Nothing but bench-local events past the cursor — still advance past them.
        _save_cursor(
            cursor_path, _advance_cursor(cursor, read_segments(events_dir, cursor=cursor), set())
        )
        return None
    disp = _post_ingest(url, token, _to_ipc_bytes(table), timeout=timeout)
    rejected = {str(x) for x in disp.get("rejected_ids", [])}
    _save_cursor(cursor_path, _advance_cursor(cursor, table, rejected))
    return disp


# --------------------------------------------------------------------------- #
# Channel segments (opt-in via --channels)                                    #
# --------------------------------------------------------------------------- #

# Envelope columns a closed segment carries alongside its payload (mirrors
# ``ChannelIndex._INDEX_ENVELOPE`` — kept as its own copy here since that one
# is a private implementation detail of the index, not a shared constant).
_SEGMENT_ENVELOPE = frozenset(
    {"received_at", "sampled_at", "source_method", "session_id", "sample_interval", "sample_offset"}
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
    url: str, token: str, channel_id: str, table: pa.Table, *, timeout: float
) -> dict:
    """POST one closed segment to the proposed ``/ingest/channels/{channel_id}``
    endpoint (Arrow IPC body, same transport as events' ``/ingest``). REVIEW
    NEEDED: this endpoint does not exist on the server yet (see module
    docstring) — response shape assumed to mirror
    ``ingest_channel_segment``'s return, ``{"segment_key", "row_count"}``.
    """
    body = _to_ipc_bytes(table)
    req = urllib.request.Request(
        url.rstrip("/") + f"/ingest/channels/{urllib.parse.quote(channel_id, safe='')}",
        data=body,
        method="POST",
        headers={"Content-Type": _ARROW_CONTENT_TYPE, "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — our own server URL
        return json.loads(resp.read().decode("utf-8"))


def _forward_channels_once(
    channels_dir: Path, cursor_path: Path, url: str, token: str, *, timeout: float
) -> dict | None:
    """Forward every closed channel segment not yet in the durable cursor.

    Persists the cursor after EACH accepted segment (not batched at the end):
    since a channel segment has no server-side dedup key (every accepted POST
    always creates a new object — see ``ingest_channel_segment``'s docstring),
    the only exactly-once guard is this bench never resending a path it has
    already gotten a 2xx for. Persisting per-segment bounds a crash's replay
    window to at most the one segment in flight, rather than the whole batch.
    A raised exception (network/HTTP error) stops the pass without recording
    that segment — it re-sends next poll, same as the events path.
    """
    from testerkit.replication import read_closed_channel_segments

    sent = _load_channels_cursor(cursor_path)
    segments = read_closed_channel_segments(channels_dir, sent)
    if not segments:
        return None
    forwarded = 0
    rows = 0
    for seg in segments:
        wire = _channel_wire_table(seg)
        disp = _post_channel_segment(url, token, seg.channel_id, wire, timeout=timeout)
        sent.add(seg.rel_path)
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
    files_dir: Path, cursor_path: Path, url: str, token: str, *, timeout: float
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

    sent_uris, sent_hashes = _load_files_cursor(cursor_path)
    records = read_new_file_records(files_dir, sent_uris)
    if not records:
        return None
    forwarded = 0
    skipped = 0
    for rec in records:
        content_hash = hashlib.sha256(rec.data).hexdigest()
        if content_hash in sent_hashes:
            sent_uris.add(rec.uri)
            _save_files_cursor(cursor_path, sent_uris, sent_hashes)
            skipped += 1
            continue
        _post_file_blob(url, token, rec, timeout=timeout)
        sent_uris.add(rec.uri)
        sent_hashes.add(content_hash)
        _save_files_cursor(cursor_path, sent_uris, sent_hashes)
        forwarded += 1
    return {"files": forwarded, "skipped_dupe": skipped}


def _forward_all_once(  # noqa: PLR0913
    events_dir: Path,
    events_cursor_path: Path,
    channels_dir: Path,
    channels_cursor_path: Path,
    files_dir: Path,
    files_cursor_path: Path,
    url: str,
    token: str,
    *,
    timeout: float,
    channels: bool,
    files: bool,
) -> dict:
    """Run one poll pass over every enabled store.

    Events always run — this is exactly the original (pre-Part-B) behavior,
    unconditional. Channels/files run only when their flag is enabled, so a
    plain ``testerkit forward`` (``channels=False, files=False``) does exactly
    what it did before either existed: one ``_forward_once`` call, nothing
    else. Any store's failure raises out of this function immediately (the
    caller's existing retry/backoff handles it exactly as it did for events
    alone — a channel/file forward failure never silently swallows; it also
    never blocks a store that already sent this pass, since each has already
    advanced its own cursor by the time a later store raises).
    """
    result: dict[str, dict] = {}
    disp = _forward_once(events_dir, events_cursor_path, url, token, timeout=timeout)
    if disp is not None:
        result["events"] = disp
    if channels:
        cdisp = _forward_channels_once(
            channels_dir, channels_cursor_path, url, token, timeout=timeout
        )
        if cdisp is not None:
            result["channels"] = cdisp
    if files:
        fdisp = _forward_files_once(files_dir, files_cursor_path, url, token, timeout=timeout)
        if fdisp is not None:
            result["files"] = fdisp
    return result


@main.command()
@click.option("--url", default=None, help=f"Server ingest base URL (or ${_URL_ENV})")
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
    default=False,
    help="Also forward closed channel segments (off by default — events-only otherwise; "
    "REVIEW NEEDED, see module docstring)",
)
@click.option(
    "--files/--no-files",
    default=False,
    help="Also forward new file blobs + sidecars (off by default — events-only otherwise; "
    "REVIEW NEEDED, see module docstring)",
)
def forward(  # noqa: PLR0913
    url: str | None,
    data_dir: str | None,
    interval: float,
    timeout: float,
    once: bool,
    channels: bool,
    files: bool,
):
    """Forward this bench's event WAL to a central server (store-and-forward).

    With ``--channels`` / ``--files``, also forwards closed channel segments
    and new file blobs (docs/22 Part B) — off by default, so a plain
    ``testerkit forward`` behaves exactly as it did before either existed.
    """
    from testerkit.data.data_dir import resolve_data_dir

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = url or os.environ.get(_URL_ENV)
    token = os.environ.get(_TOKEN_ENV)
    if not server:
        raise click.ClickException(f"a server URL is required (--url or ${_URL_ENV})")
    if not token:
        raise click.ClickException(f"a machine token is required in ${_TOKEN_ENV}")

    resolved = resolve_data_dir(Path(data_dir) if data_dir else None)
    events_dir = resolved / "events"
    cursor_path = events_dir / "_forward_cursor.json"
    channels_dir = resolved / "channels"
    channels_cursor_path = channels_dir / "_forward_cursor.json"
    files_dir = resolved / "files"
    files_cursor_path = files_dir / "_forward_cursor.json"
    log.info("forwarding %s → %s", events_dir, server)
    if channels:
        log.info("forwarding channel segments: %s → %s", channels_dir, server)
    if files:
        log.info("forwarding file blobs: %s → %s", files_dir, server)

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
                server,
                token,
                timeout=timeout,
                channels=channels,
                files=files,
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
