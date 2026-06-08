"""Files catalog daemon manager + client helpers.

Subclasses ``DaemonManager`` for the files catalog DuckDB daemon and
provides the client-side query/push/discovery helpers consumers use to
reach the daemon's warm catalog (req 2) instead of walking the tree.

Blobs + ``.meta.json`` sidecars are the durable truth; the in-memory
catalog is rebuilt from them on every daemon start.
"""

from __future__ import annotations

import json
import threading
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.flight as flight

from litmus.data._daemon_lifecycle import DaemonManager, _pid_alive
from litmus.data._flight_query import (
    FlightQueryClient,
    _drop_pooled_client,
    _get_pooled_client,
    call_options,
)
from litmus.data.files.catalog import (
    CATALOG_ARROW_SCHEMA,
    FRAME_ARROW_SCHEMA,
    FRAMES_DB,
)


class FilesCatalogManager(DaemonManager):
    """Manages the files catalog DuckDB daemon."""

    _state_name = "_files_catalog.json"
    _lock_name = "_files_catalog.lock"
    _ready_name = "_files_catalog_ready"
    _pid_name = "_files_catalog_pid"
    _daemon_module = "litmus.data.files._catalog_daemon"
    _port_file = "_files_catalog_flight_port"


def acquire(files_dir: Path) -> str:
    """Acquire a ref to the catalog daemon, starting it if needed.

    Returns the gRPC location string for Flight queries.
    """
    mgr = FilesCatalogManager(files_dir)
    mgr.acquire()
    deadline = time.monotonic() + 5.0
    while True:
        location = mgr.read_state().get("location")
        if location:
            return location
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"files catalog daemon started but no location in state after 5s: {files_dir}"
            )
        time.sleep(0.05)


def release(files_dir: Path) -> None:
    """Release our reference to the catalog daemon."""
    FilesCatalogManager(files_dir).release()


def is_running(files_dir: Path) -> bool:
    """Return True iff a live catalog daemon is serving ``files_dir``.

    Inspection only — never spawns. Consumers use this to prefer the
    warm catalog when a daemon is up, and fall back to the local walk
    otherwise (so unit tests with throwaway data dirs don't spawn a
    per-test daemon). Phase E removes the walk fallback entirely.
    """
    state = files_dir / FilesCatalogManager._state_name
    if not state.exists():
        return False
    try:
        data = json.loads(state.read_text())
        pid = data.get("pid")
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(pid, int) and _pid_alive(pid)


def _sql_str(value: str) -> str:
    return value.replace("'", "''")


def query_catalog(files_dir: Path, sql: str) -> list[dict[str, Any]]:
    """Run a catalog SQL query against the daemon (acquires + releases)."""
    location = acquire(files_dir)
    try:
        client = FlightQueryClient(
            location,
            "files",
            reacquire=lambda: acquire(files_dir),
            label="FileCatalog",
        )
        return client.query(sql)
    finally:
        release(files_dir)


def resolve_uri(files_dir: Path, uri: str) -> str | None:
    """Resolve a ``file://`` URI to its on-disk path via the warm catalog."""
    rows = query_catalog(
        files_dir,
        f"SELECT path FROM file_catalog WHERE uri = '{_sql_str(uri)}' LIMIT 1",
    )
    return rows[0]["path"] if rows else None


def list_recent(files_dir: Path, limit: int) -> list[dict[str, Any]]:
    """Return the ``limit`` most-recent catalog rows, newest first."""
    return query_catalog(
        files_dir,
        f"SELECT * FROM file_catalog ORDER BY created_at DESC LIMIT {int(limit)}",
    )


def push_artifact(files_dir: Path, row: dict[str, Any]) -> None:
    """Push one catalog row to the daemon (best-effort, non-fatal).

    Called by ``FileStore.write`` after the blob + sidecar land durably.
    Non-fatal: the sidecar is the durable truth, so a failed push just
    means the artifact isn't in the warm catalog until the next daemon
    restart rescans it. Skips silently when no daemon is running, so
    plain writes never spawn one.
    """
    if not is_running(files_dir):
        return
    location = acquire(files_dir)
    try:
        client = _get_pooled_client(location)
        tbl = pa.Table.from_pylist([row], schema=CATALOG_ARROW_SCHEMA)
        descriptor = flight.FlightDescriptor.for_command(b"files\0file_catalog")
        writer, reader = client.do_put(descriptor, tbl.schema, options=call_options())
        writer.write_table(tbl)
        # Drain the server ACK(s) before returning: each ACK confirms the daemon
        # committed one batch, so a resolve_uri right after write() is guaranteed
        # to see the row (read-after-write) instead of racing the insert.
        for _ in tbl.to_batches():
            reader.read()
        writer.close()
    except (OSError, RuntimeError, pa.ArrowException) as exc:
        _drop_pooled_client(location)
        warnings.warn(f"Files catalog push failed (non-fatal): {exc}", stacklevel=2)
    finally:
        release(files_dir)


def publish_frame(
    files_dir: Path,
    *,
    stream_id: str,
    uri: str,
    byte_offset: int,
    length: int,
) -> None:
    """Publish one ephemeral stream-frame notification (best-effort).

    Called by a streaming sink after each ``write``. Fans out to live
    subscribers so they range-read ``[byte_offset, byte_offset+length)``
    of the growing artifact (req 5: no poll). NOT persisted — the on-disk
    artifact is the durable record. Skips silently when no daemon runs,
    so plain streaming never spawns one.
    """
    if not is_running(files_dir):
        return
    location = acquire(files_dir)
    try:
        client = _get_pooled_client(location)
        tbl = pa.Table.from_pylist(
            [
                {
                    "stream_id": stream_id,
                    "uri": uri,
                    "byte_offset": byte_offset,
                    "length": length,
                }
            ],
            schema=FRAME_ARROW_SCHEMA,
        )
        descriptor = flight.FlightDescriptor.for_command(f"{FRAMES_DB}\0frames".encode())
        writer, _ = client.do_put(descriptor, tbl.schema, options=call_options())
        writer.write_table(tbl)
        writer.close()
    except (OSError, RuntimeError, pa.ArrowException) as exc:
        _drop_pooled_client(location)
        warnings.warn(f"Files frame publish failed (non-fatal): {exc}", stacklevel=2)
    finally:
        release(files_dir)


def subscribe_frames(
    files_dir: Path,
    callback: Callable[[dict[str, Any]], None],
) -> Callable[[], None]:
    """Subscribe to live stream-frame notifications. Returns an unsub callable.

    Spawns a reader thread that calls ``callback`` with each frame dict
    (``stream_id``/``uri``/``byte_offset``/``length``). Holds a daemon
    ref for the subscription's lifetime; ``unsub`` closes the stream and
    releases it. Uses a dedicated client so closing it cleanly interrupts
    the held-open ``do_get``.
    """
    location = acquire(files_dir)
    client = flight.connect(location)
    stop = threading.Event()

    def _run() -> None:
        try:
            reader = client.do_get(flight.Ticket(f"{FRAMES_DB}\0__SUBSCRIBE__".encode()))
            for chunk in reader:
                if stop.is_set():
                    break
                batch = chunk.data
                for i in range(batch.num_rows):
                    callback({c: batch.column(c)[i].as_py() for c in batch.schema.names})
        except (OSError, pa.ArrowException):
            pass

    thread = threading.Thread(target=_run, daemon=True, name="files-frame-sub")
    thread.start()

    def unsub() -> None:
        stop.set()
        try:
            client.close()
        except (OSError, RuntimeError, pa.ArrowException):
            pass
        release(files_dir)

    return unsub
