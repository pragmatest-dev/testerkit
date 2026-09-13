"""``testerkit forward`` — store-and-forward this bench's event WAL to a server.

A thin loop over the public replication surface: read complete event batches from the
local WAL past a durable cursor, POST them to a central server's authed ``/ingest``, and
advance the cursor only on rows the server accepted. Exactly-once falls out of the
server's ``id`` dedup, so a crash-and-resume simply re-sends and de-dupes.

Meant to run standing (systemd/container) — it is NOT a DaemonManager daemon. Auth is a
per-bench machine token in ``TESTERKIT_FORWARD_TOKEN``; the server URL is ``--url`` or
``TESTERKIT_FORWARD_URL``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import click

from testerkit.cli.root import main

_TOKEN_ENV = "TESTERKIT_FORWARD_TOKEN"
_URL_ENV = "TESTERKIT_FORWARD_URL"
_ARROW_CONTENT_TYPE = "application/vnd.apache.arrow.stream"
# The bench's own derivation signal — dropped so the SERVER re-derives runs itself
# (forwarding it would evict the server's accumulator before it materializes).
_BENCH_LOCAL_EVENT_TYPES = frozenset({"run.materialized"})

log = logging.getLogger("testerkit.forward")


def _load_cursor(path: Path) -> dict[str, int]:
    try:
        raw = json.loads(path.read_text())
        return {str(k): int(v) for k, v in raw.items()}
    except (OSError, ValueError):
        return {}


def _save_cursor(path: Path, cursor: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="._fwd-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cursor, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


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
def forward(url: str | None, data_dir: str | None, interval: float, timeout: float, once: bool):
    """Forward this bench's event WAL to a central server (store-and-forward)."""
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
    log.info("forwarding %s → %s", events_dir, server)

    backoff = interval
    while True:
        try:
            disp = _forward_once(events_dir, cursor_path, server, token, timeout=timeout)
            backoff = interval  # reset after a clean pass
            if disp is not None:
                log.info(
                    "forwarded: inserted=%s deduped=%s rejected=%s",
                    disp.get("inserted"),
                    disp.get("deduped"),
                    len(disp.get("rejected_ids", [])),
                )
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
